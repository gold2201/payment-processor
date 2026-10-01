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

    async def _update_status(payment, status):
        events.append("update_status")
        payment.status = status
        payment.processed_at = datetime.now(UTC)
        return payment

    repo = SimpleNamespace(
        get=AsyncMock(),
        get_by_id_for_update=AsyncMock(),
        update_status=AsyncMock(side_effect=_update_status),
    )
    uow = make_uow(payments=repo)
    uow.commit = AsyncMock(side_effect=lambda: events.append("commit"))

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
    setup.repo.get.return_value = None

    result = await setup.service.process_payment_created(uuid4())

    assert result.state == ProcessingState.NOT_FOUND
    setup.gateway.assert_not_awaited()
    setup.repo.update_status.assert_not_awaited()


async def test_already_processed_payment_is_not_touched(setup, make_payment):
    payment = make_payment(status=PaymentStatus.SUCCEEDED, processed_at=datetime.now(UTC))
    setup.repo.get.return_value = payment

    result = await setup.service.process_payment_created(payment.payment_id)

    assert result.state == ProcessingState.ALREADY_PROCESSED
    assert result.webhook_payload["status"] == "succeeded"
    setup.gateway.assert_not_awaited()
    setup.repo.update_status.assert_not_awaited()


async def test_successful_processing_updates_status_and_builds_webhook(setup, make_payment):
    payment = make_payment()
    setup.repo.get.return_value = payment
    setup.repo.get_by_id_for_update.return_value = payment

    result = await setup.service.process_payment_created(payment.payment_id)

    assert result.state == ProcessingState.PROCESSED
    assert result.webhook_url == "https://example.com/hook"
    assert result.webhook_payload == {
        "payment_id": str(payment.payment_id),
        "status": "succeeded",
        "amount": str(Decimal("100.50")),
        "currency": "USD",
        "processed_at": payment.processed_at.isoformat(),
    }
    setup.repo.update_status.assert_awaited_once_with(payment, PaymentStatus.SUCCEEDED)


async def test_gateway_failure_is_stored_as_failed_status(setup, make_payment):
    payment = make_payment()
    setup.repo.get.return_value = payment
    setup.repo.get_by_id_for_update.return_value = payment
    setup.gateway.side_effect = None
    setup.gateway.return_value = PaymentStatus.FAILED

    result = await setup.service.process_payment_created(payment.payment_id)

    assert result.state == ProcessingState.PROCESSED
    assert result.webhook_payload["status"] == "failed"


async def test_transaction_is_closed_before_calling_gateway(setup, make_payment):
    payment = make_payment()
    setup.repo.get.return_value = payment
    setup.repo.get_by_id_for_update.return_value = payment

    await setup.service.process_payment_created(payment.payment_id)

    assert setup.events == ["commit", "gateway", "update_status", "commit"]


async def test_concurrent_consumer_wins_the_race(setup, make_payment):
    pending = make_payment()
    processed_elsewhere = make_payment(
        payment_id=pending.payment_id,
        status=PaymentStatus.FAILED,
        processed_at=datetime.now(UTC),
    )
    setup.repo.get.return_value = pending
    setup.repo.get_by_id_for_update.return_value = processed_elsewhere

    result = await setup.service.process_payment_created(pending.payment_id)

    assert result.state == ProcessingState.ALREADY_PROCESSED
    assert result.webhook_payload["status"] == "failed"
    setup.repo.update_status.assert_not_awaited()


async def test_payment_deleted_during_processing(setup, make_payment):
    payment = make_payment()
    setup.repo.get.return_value = payment
    setup.repo.get_by_id_for_update.return_value = None

    result = await setup.service.process_payment_created(payment.payment_id)

    assert result.state == ProcessingState.NOT_FOUND
