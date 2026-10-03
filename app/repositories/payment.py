from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select, update

from app.common.enums import Currency, PaymentStatus
from app.models.payment import Payment
from app.repositories.base import BaseRepository

CLAIM_TTL_SECONDS = 60


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

    async def claim_for_processing(self, payment_id: UUID) -> Payment | None:
        now = datetime.now(UTC)
        stale_before = now - timedelta(seconds=CLAIM_TTL_SECONDS)
        stmt = (
            update(Payment)
            .where(Payment.payment_id == payment_id)
            .where(Payment.processed_at.is_(None))
            .where((Payment.processing_started_at.is_(None)) | (Payment.processing_started_at < stale_before))
            .values(processing_started_at=now)
            .returning(Payment)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def finalize_processing(
        self,
        payment_id: UUID,
        status: PaymentStatus,
    ) -> Payment | None:
        now = datetime.now(UTC)
        stmt = (
            update(Payment)
            .where(Payment.payment_id == payment_id)
            .where(Payment.processed_at.is_(None))
            .values(status=status, processed_at=now)
            .returning(Payment)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()
