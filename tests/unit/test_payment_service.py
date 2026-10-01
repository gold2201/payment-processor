from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from app.common.enums import Currency, PaymentStatus
from app.schemas.payment import PaymentCreateSchema
from app.services import payments as payments_module
from app.services.payments import PaymentService
from sqlalchemy.exc import IntegrityError


def _payload() -> PaymentCreateSchema:
    return PaymentCreateSchema(
        amount=Decimal("100.50"),
        currency=Currency.USD,
        description="Order #42",
        metadata={"order_id": "42"},
        webhook_url="https://example.com/hook",
    )


def _duplicate_error() -> IntegrityError:
    return IntegrityError("INSERT INTO payments", {}, Exception("duplicate key value"))


@pytest.fixture
def setup(monkeypatch, make_uow):
    repo = SimpleNamespace(
        get=AsyncMock(),
        get_by_idempotency_key=AsyncMock(return_value=None),
        create=AsyncMock(),
    )
    outbox = SimpleNamespace(create_payment_created=AsyncMock())
    uow = make_uow(payments=repo, outbox=outbox)
    monkeypatch.setattr(payments_module, "UnitOfWork", lambda _session: uow)
    session = SimpleNamespace(rollback=AsyncMock())
    return SimpleNamespace(service=PaymentService(session), repo=repo, outbox=outbox, uow=uow)


async def test_creates_payment_and_outbox_event_in_one_commit(setup, make_payment):
    payment = make_payment()
    setup.repo.create.return_value = payment

    dto = await setup.service.create_payment(data=_payload(), idempotency_key="key-1")

    setup.repo.create.assert_awaited_once()
    kwargs = setup.repo.create.await_args.kwargs
    assert kwargs["idempotency_key"] == "key-1"
    assert kwargs["amount"] == Decimal("100.50")
    assert kwargs["currency"] == Currency.USD
    assert kwargs["metadata"] == {"order_id": "42"}
    assert kwargs["webhook_url"] == "https://example.com/hook"

    setup.outbox.create_payment_created.assert_awaited_once_with(payment.payment_id)
    setup.uow.commit.assert_awaited_once()
    assert dto.payment_id == payment.payment_id
    assert dto.status == PaymentStatus.PENDING


async def test_same_key_returns_existing_payment_without_side_effects(setup, make_payment):
    existing = make_payment()
    setup.repo.get_by_idempotency_key.return_value = existing

    dto = await setup.service.create_payment(data=_payload(), idempotency_key="key-1")

    assert dto.payment_id == existing.payment_id
    setup.repo.create.assert_not_awaited()
    setup.outbox.create_payment_created.assert_not_awaited()
    setup.uow.commit.assert_not_awaited()


async def test_race_on_unique_key_returns_winner_payment(setup, make_payment):
    winner = make_payment()

    setup.repo.get_by_idempotency_key.side_effect = [None, winner]
    setup.repo.create.side_effect = _duplicate_error()

    dto = await setup.service.create_payment(data=_payload(), idempotency_key="key-1")

    assert dto.payment_id == winner.payment_id
    setup.outbox.create_payment_created.assert_not_awaited()
    setup.uow.commit.assert_not_awaited()


async def test_integrity_error_is_reraised_if_payment_still_not_found(setup):
    setup.repo.get_by_idempotency_key.side_effect = [None, None]
    setup.repo.create.side_effect = _duplicate_error()

    with pytest.raises(IntegrityError):
        await setup.service.create_payment(data=_payload(), idempotency_key="key-1")


async def test_get_payment_returns_none_for_unknown_id(setup, make_payment):
    setup.repo.get.return_value = None

    assert await setup.service.get_payment(make_payment().payment_id) is None


async def test_get_payment_maps_orm_metadata_to_dto(setup, make_payment):
    payment = make_payment(metadata_={"order_id": "42"})
    setup.repo.get.return_value = payment

    dto = await setup.service.get_payment(payment.payment_id)

    assert dto is not None
    assert dto.metadata == {"order_id": "42"}
