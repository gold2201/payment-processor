from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from app.common.enums import Currency, PaymentStatus


class FakeUoW:
    def __init__(self, *, payments: Any = None, outbox: Any = None) -> None:
        self.payments = payments
        self.outbox = outbox
        self.commit = AsyncMock()
        self.flush = AsyncMock()
        self.rollback = AsyncMock()

    async def __aenter__(self) -> "FakeUoW":
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if exc_type is not None:
            await self.rollback()


@pytest.fixture
def make_uow():
    def _make(**kwargs: Any) -> FakeUoW:
        return FakeUoW(**kwargs)

    return _make


@pytest.fixture
def make_payment():
    def _make(**overrides: Any) -> SimpleNamespace:
        data: dict[str, Any] = {
            "payment_id": uuid4(),
            "amount": Decimal("100.50"),
            "currency": Currency.USD,
            "description": "Order #42",
            "metadata_": {"order_id": "42"},
            "status": PaymentStatus.PENDING,
            "idempotency_key": "key-1",
            "webhook_url": "https://example.com/hook",
            "created_at": datetime(2026, 10, 1, 12, 0, tzinfo=UTC),
            "processed_at": None,
        }
        data.update(overrides)
        return SimpleNamespace(**data)

    return _make
