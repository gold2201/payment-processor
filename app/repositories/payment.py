from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select

from app.common.enums import Currency
from app.models.payment import Payment
from app.repositories.base import BaseRepository


class PaymentRepository(BaseRepository):
    async def get(self, payment_id: UUID) -> Payment | None:
        return await self._session.get(Payment, payment_id)

    async def get_by_idempotency_key(self, key: str) -> Payment | None:
        result = await self._session.execute(select(Payment).where(Payment.idempotency_key == key))
        return result.scalar_one_or_none()

    async def create(
        self,
        *,
        amount: Decimal,
        currency: Currency,
        description: str | None,
        metadata: dict[str, Any],
        idempotency_key: str,
        webhook_url: str | None,
    ) -> Payment:
        payment = Payment(
            amount=amount,
            currency=currency,
            description=description,
            metadata_=metadata,
            idempotency_key=idempotency_key,
            webhook_url=webhook_url,
        )
        self._session.add(payment)
        await self._session.flush()
        return payment
