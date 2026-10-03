from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from app.core.settings import settings
from app.services import outbox as outbox_module
from app.services.outbox import OutboxService


def _event(retry_count: int = 0) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        payload={"payment_id": str(uuid4())},
        retry_count=retry_count,
    )


@pytest.fixture
def setup(monkeypatch, make_uow):
    repo = SimpleNamespace(
        reclaim_stuck=AsyncMock(return_value=0),
        claim_pending_batch=AsyncMock(return_value=[]),
        mark_published_by_id=AsyncMock(),
        schedule_retry_by_id=AsyncMock(),
        mark_failed_by_id=AsyncMock(),
    )
    uow = make_uow(outbox=repo)
    publish_new = AsyncMock()
    publish_dlq = AsyncMock()

    monkeypatch.setattr(outbox_module, "UnitOfWork", lambda _session: uow)
    monkeypatch.setattr(outbox_module, "publish_payment_new", publish_new)
    monkeypatch.setattr(outbox_module, "publish_payment_to_dlq", publish_dlq)
    monkeypatch.setattr(settings, "OUTBOX_MAX_ATTEMPTS", 3)
    monkeypatch.setattr(settings, "OUTBOX_BASE_RETRY_DELAY_SECONDS", 3)
    monkeypatch.setattr(settings, "OUTBOX_PUBLISH_TIMEOUT_SECONDS", 5.0)

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

    setup.uow.commit.assert_awaited_once()
    setup.repo.reclaim_stuck.assert_awaited_once()


async def test_published_event_is_marked_and_committed(setup):
    event = _event()
    setup.repo.claim_pending_batch.return_value = [event]

    result = await setup.service.dispatch_pending()

    setup.publish_new.assert_awaited_once_with(event.payload, message_id=str(event.id))
    setup.repo.mark_published_by_id.assert_awaited_once_with(event.id)

    assert setup.uow.commit.await_count == 2
    assert (result.selected, result.sent, result.failed) == (1, 1, 0)


async def test_first_failure_schedules_retry_with_backoff(setup):
    event = _event(retry_count=0)
    setup.repo.claim_pending_batch.return_value = [event]
    setup.publish_new.side_effect = RuntimeError("rabbit is down")

    before = datetime.now(UTC)
    result = await setup.service.dispatch_pending()

    setup.repo.schedule_retry_by_id.assert_awaited_once()
    call_kwargs = setup.repo.schedule_retry_by_id.await_args.kwargs
    call_args = setup.repo.schedule_retry_by_id.await_args.args

    assert call_args[0] == event.id
    assert call_kwargs["attempts"] == 1

    assert before <= call_kwargs["next_retry_at"] <= datetime.now(UTC) + timedelta(seconds=3)
    setup.repo.mark_failed_by_id.assert_not_awaited()
    setup.publish_dlq.assert_not_awaited()

    assert setup.uow.commit.await_count == 2
    assert (result.sent, result.failed) == (0, 0)


async def test_event_goes_to_failed_and_dlq_after_last_attempt(setup):
    event = _event(retry_count=2)
    setup.repo.claim_pending_batch.return_value = [event]
    setup.publish_new.side_effect = RuntimeError("rabbit is down")

    result = await setup.service.dispatch_pending()

    setup.repo.mark_failed_by_id.assert_awaited_once()
    assert setup.repo.mark_failed_by_id.await_args.kwargs["attempts"] == 3
    assert setup.repo.mark_failed_by_id.await_args.args[0] == event.id
    setup.repo.schedule_retry_by_id.assert_not_awaited()
    setup.publish_dlq.assert_awaited_once()
    assert setup.publish_dlq.await_args.kwargs["message_id"] == str(event.id)

    assert setup.uow.commit.await_count == 2
    assert result.failed == 1


async def test_dlq_failure_does_not_break_dispatch(setup):
    event = _event(retry_count=2)
    setup.repo.claim_pending_batch.return_value = [event]
    setup.publish_new.side_effect = RuntimeError("rabbit is down")
    setup.publish_dlq.side_effect = RuntimeError("dlq is down too")

    result = await setup.service.dispatch_pending()

    setup.repo.mark_failed_by_id.assert_awaited_once()
    assert setup.uow.commit.await_count == 2
    assert result.failed == 1


async def test_failing_event_does_not_block_the_next_one(setup):
    bad, good = _event(), _event()
    setup.repo.claim_pending_batch.return_value = [bad, good]
    setup.publish_new.side_effect = [RuntimeError("boom"), None]

    result = await setup.service.dispatch_pending()

    setup.repo.schedule_retry_by_id.assert_awaited_once()
    assert setup.repo.schedule_retry_by_id.await_args.args[0] == bad.id

    setup.repo.mark_published_by_id.assert_awaited_once_with(good.id)

    assert setup.uow.commit.await_count == 3
    assert (result.selected, result.sent) == (2, 1)


async def test_reclaim_stuck_is_invoked_and_logged(setup, caplog):
    setup.repo.reclaim_stuck.return_value = 5

    import logging

    with caplog.at_level(logging.WARNING):
        await setup.service.dispatch_pending()

    setup.repo.reclaim_stuck.assert_awaited_once()

    assert any("reclaim" in rec.message.lower() for rec in caplog.records)
