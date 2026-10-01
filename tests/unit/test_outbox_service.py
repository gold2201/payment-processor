from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from app.core.settings import settings
from app.services import outbox as outbox_module
from app.services.outbox import OutboxService


def _event(retry_count: int = 0) -> SimpleNamespace:
    return SimpleNamespace(id=uuid4(), payload={"payment_id": str(uuid4())}, retry_count=retry_count)


@pytest.fixture
def setup(monkeypatch, make_uow):
    repo = SimpleNamespace(
        get_ready_for_dispatch=AsyncMock(return_value=[]),
        mark_published=AsyncMock(),
        schedule_retry=AsyncMock(),
        mark_failed=AsyncMock(),
    )
    uow = make_uow(outbox=repo)
    publish_new = AsyncMock()
    publish_dlq = AsyncMock()

    monkeypatch.setattr(outbox_module, "UnitOfWork", lambda _session: uow)
    monkeypatch.setattr(outbox_module, "publish_payment_new", publish_new)
    monkeypatch.setattr(outbox_module, "publish_payment_to_dlq", publish_dlq)
    monkeypatch.setattr(settings, "OUTBOX_MAX_ATTEMPTS", 3)
    monkeypatch.setattr(settings, "OUTBOX_BASE_RETRY_DELAY_SECONDS", 3)

    return SimpleNamespace(
        service=OutboxService(session=None),  # type: ignore[arg-type]
        repo=repo,
        uow=uow,
        publish_new=publish_new,
        publish_dlq=publish_dlq,
    )


async def test_nothing_to_dispatch(setup):
    result = await setup.service.dispatch_pending()

    assert (result.selected, result.sent, result.failed) == (0, 0, 0)
    setup.publish_new.assert_not_awaited()
    setup.uow.commit.assert_not_awaited()


async def test_published_event_is_marked_and_committed(setup):
    event = _event()
    setup.repo.get_ready_for_dispatch.return_value = [event]

    result = await setup.service.dispatch_pending()

    setup.publish_new.assert_awaited_once_with(event.payload, message_id=str(event.id))
    setup.repo.mark_published.assert_awaited_once_with(event)
    setup.uow.commit.assert_awaited_once()
    assert (result.selected, result.sent, result.failed) == (1, 1, 0)


async def test_first_failure_schedules_retry_with_backoff(setup):
    event = _event(retry_count=0)
    setup.repo.get_ready_for_dispatch.return_value = [event]
    setup.publish_new.side_effect = RuntimeError("rabbit is down")

    before = datetime.now(UTC)
    result = await setup.service.dispatch_pending()

    setup.repo.schedule_retry.assert_awaited_once()
    kwargs = setup.repo.schedule_retry.await_args.kwargs
    assert kwargs["attempts"] == 1

    assert before <= kwargs["next_retry_at"] <= datetime.now(UTC) + timedelta(seconds=3)
    setup.repo.mark_failed.assert_not_awaited()
    setup.publish_dlq.assert_not_awaited()
    setup.uow.commit.assert_awaited_once()
    assert (result.sent, result.failed) == (0, 0)


async def test_event_goes_to_failed_and_dlq_after_last_attempt(setup):
    event = _event(retry_count=2)  # это будет третья попытка
    setup.repo.get_ready_for_dispatch.return_value = [event]
    setup.publish_new.side_effect = RuntimeError("rabbit is down")

    result = await setup.service.dispatch_pending()

    setup.repo.mark_failed.assert_awaited_once()
    assert setup.repo.mark_failed.await_args.kwargs["attempts"] == 3
    setup.repo.schedule_retry.assert_not_awaited()
    setup.publish_dlq.assert_awaited_once()
    assert setup.publish_dlq.await_args.kwargs["message_id"] == str(event.id)
    setup.uow.commit.assert_awaited_once()
    assert result.failed == 1


async def test_dlq_failure_does_not_break_dispatch(setup):
    event = _event(retry_count=2)
    setup.repo.get_ready_for_dispatch.return_value = [event]
    setup.publish_new.side_effect = RuntimeError("rabbit is down")
    setup.publish_dlq.side_effect = RuntimeError("dlq is down too")

    result = await setup.service.dispatch_pending()

    setup.repo.mark_failed.assert_awaited_once()
    setup.uow.commit.assert_awaited_once()
    assert result.failed == 1


async def test_failing_event_does_not_block_the_next_one(setup):
    bad, good = _event(), _event()
    setup.repo.get_ready_for_dispatch.return_value = [bad, good]
    setup.publish_new.side_effect = [RuntimeError("boom"), None]

    result = await setup.service.dispatch_pending()

    setup.repo.schedule_retry.assert_awaited_once()
    setup.repo.mark_published.assert_awaited_once_with(good)
    assert (result.selected, result.sent) == (2, 1)
