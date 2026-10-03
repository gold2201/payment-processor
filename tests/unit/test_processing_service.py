from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from app.common.enums import PaymentStatus, ProcessingState
from app.services import processing as processing_module
from app.services.processing import PaymentProcessingService


@pytest.fixture
def setup(monkeypatch, make_uow):
    events: list[str] = []

    repo = SimpleNamespace(
        claimed_payment=None,
        current_payment=None,
        finalized_payment=None,
        claim_for_processing=AsyncMock(),
        finalize_processing=AsyncMock(),
        get=AsyncMock(),
    )

    async def _claim(_payment_id):
        events.append("claim")
        return repo.claimed_payment

    async def _finalize(_payment_id, _status):
        events.append("finalize")
        return repo.finalized_payment

    async def _get(_payment_id):
        events.append("get")
        return repo.current_payment

    repo.claim_for_processing.side_effect = _claim
    repo.finalize_processing.side_effect = _finalize
    repo.get.side_effect = _get

    uow = make_uow(payments=repo)
    uow.commit = AsyncMock(side_effect=lambda: events.append("commit"))
    uow.rollback = AsyncMock(side_effect=lambda: events.append("rollback"))

    async def _gateway():
        events.append("gateway")
        return PaymentStatus.SUCCEEDED

    gateway = AsyncMock(side_effect=_gateway)

    monkeypatch.setattr(processing_module, "UnitOfWork", lambda _session: uow)
    monkeypatch.setattr(processing_module, "_simulate_gateway", gateway)

    return SimpleNamespace(
        service=PaymentProcessingService(session=None),  # type: ignore[arg-type]
        repo=repo,
        uow=uow,
        gateway=gateway,
        events=events,
    )


async def test_unknown_payment(setup):
    setup.repo.claimed_payment = None
    setup.repo.current_payment = None

    result = await setup.service.process_payment_created(uuid4())

    assert result.state == ProcessingState.NOT_FOUND
    setup.gateway.assert_not_awaited()
    setup.repo.finalize_processing.assert_not_awaited()

    assert setup.events == ["claim", "get", "commit"]


async def test_already_processed_payment_is_not_touched(setup, make_payment):
    payment = make_payment(
        status=PaymentStatus.SUCCEEDED,
        processed_at=datetime.now(UTC),
    )
    setup.repo.claimed_payment = None
    setup.repo.current_payment = payment

    result = await setup.service.process_payment_created(payment.payment_id)

    assert result.state == ProcessingState.ALREADY_PROCESSED
    assert result.webhook_payload["status"] == "succeeded"
    setup.gateway.assert_not_awaited()
    setup.repo.finalize_processing.assert_not_awaited()
    assert setup.events == ["claim", "get", "commit"]


async def test_successful_processing_updates_status_and_builds_webhook(setup, make_payment):
    payment = make_payment()
    finalized = make_payment(
        payment_id=payment.payment_id,
        status=PaymentStatus.SUCCEEDED,
        processed_at=datetime(2026, 10, 3, 12, 0, tzinfo=UTC),
    )
    setup.repo.claimed_payment = payment
    setup.repo.finalized_payment = finalized

    result = await setup.service.process_payment_created(payment.payment_id)

    assert result.state == ProcessingState.PROCESSED
    assert result.webhook_url == "https://example.com/hook"
    assert result.webhook_payload == {
        "payment_id": str(payment.payment_id),
        "status": "succeeded",
        "amount": str(Decimal("100.50")),
        "currency": "USD",
        "processed_at": finalized.processed_at.isoformat(),
    }

    setup.repo.finalize_processing.assert_awaited_once_with(payment.payment_id, PaymentStatus.SUCCEEDED)

    assert setup.events == ["claim", "commit", "gateway", "finalize", "commit"]


async def test_gateway_failure_is_stored_as_failed_status(setup, make_payment):
    payment = make_payment()
    failed = make_payment(
        payment_id=payment.payment_id,
        status=PaymentStatus.FAILED,
        processed_at=datetime(2026, 10, 3, 12, 0, tzinfo=UTC),
    )
    setup.repo.claimed_payment = payment
    setup.repo.finalized_payment = failed
    setup.gateway.side_effect = None
    setup.gateway.return_value = PaymentStatus.FAILED

    result = await setup.service.process_payment_created(payment.payment_id)

    assert result.state == ProcessingState.PROCESSED
    assert result.webhook_payload["status"] == "failed"
    setup.repo.finalize_processing.assert_awaited_once_with(payment.payment_id, PaymentStatus.FAILED)


async def test_gateway_runs_without_open_transaction(setup, make_payment):
    payment = make_payment()
    finalized = make_payment(payment_id=payment.payment_id, status=PaymentStatus.SUCCEEDED)
    setup.repo.claimed_payment = payment
    setup.repo.finalized_payment = finalized

    await setup.service.process_payment_created(payment.payment_id)

    assert setup.events == ["claim", "commit", "gateway", "finalize", "commit"]

    assert setup.uow.commit.await_count == 2


async def test_duplicate_message_after_first_finished_does_not_call_gateway(setup, make_payment):
    payment = make_payment()
    finalized = make_payment(
        payment_id=payment.payment_id,
        status=PaymentStatus.SUCCEEDED,
        processed_at=datetime(2026, 10, 3, 12, 0, tzinfo=UTC),
    )

    setup.repo.claimed_payment = payment
    setup.repo.finalized_payment = finalized

    first = await setup.service.process_payment_created(payment.payment_id)
    assert first.state == ProcessingState.PROCESSED

    setup.repo.claimed_payment = None
    setup.repo.current_payment = finalized

    second = await setup.service.process_payment_created(payment.payment_id)

    assert second.state == ProcessingState.ALREADY_PROCESSED
    assert second.webhook_payload["status"] == "succeeded"
    setup.gateway.assert_awaited_once()
    setup.repo.finalize_processing.assert_awaited_once()


async def test_finalize_lost_race_returns_already_processed(setup, make_payment):
    payment = make_payment()
    finalized_by_other = make_payment(
        payment_id=payment.payment_id,
        status=PaymentStatus.SUCCEEDED,
        processed_at=datetime(2026, 10, 3, 12, 0, tzinfo=UTC),
    )

    setup.repo.claimed_payment = payment
    setup.repo.finalized_payment = None
    setup.repo.current_payment = finalized_by_other

    result = await setup.service.process_payment_created(payment.payment_id)

    assert result.state == ProcessingState.ALREADY_PROCESSED
    assert result.webhook_payload["status"] == "succeeded"

    assert setup.events == [
        "claim",
        "commit",
        "gateway",
        "finalize",
        "commit",
        "get",
        "commit",
    ]
    setup.gateway.assert_awaited_once()


async def test_gateway_error_rolls_back_and_propagates(setup, make_payment):
    setup.repo.claimed_payment = make_payment()
    setup.gateway.side_effect = RuntimeError("gateway exploded")

    with pytest.raises(RuntimeError, match="gateway exploded"):
        await setup.service.process_payment_created(setup.repo.claimed_payment.payment_id)

    setup.repo.finalize_processing.assert_not_awaited()

    assert setup.events == ["claim", "commit"]
