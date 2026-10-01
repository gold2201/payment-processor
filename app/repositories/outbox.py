from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select

from app.common.enums import OutboxStatus
from app.models.outbox import Outbox
from app.repositories.base import BaseRepository


class OutboxRepository(BaseRepository):
    async def create(
        self,
        *,
        event_type: str,
        payload: dict[str, Any],
    ) -> Outbox:
        event = Outbox(
            event_type=event_type,
            payload=payload,
            status=OutboxStatus.PENDING,
        )
        self._session.add(event)
        await self._session.flush()
        return event

    async def create_payment_created(self, payment_id: UUID) -> Outbox:
        return await self.create(
            event_type="payment.created",
            payload={"payment_id": str(payment_id)},
        )

    async def get_ready_for_dispatch(self, limit: int) -> list[Outbox]:
        now = datetime.now(UTC)
        stmt = (
            select(Outbox)
            .where(Outbox.status == OutboxStatus.PENDING)
            .where((Outbox.backoff_delay.is_(None)) | (Outbox.backoff_delay <= now))
            .order_by(Outbox.created_at.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def mark_published(self, event: Outbox) -> None:
        event.status = OutboxStatus.PUBLISHED
        event.published_at = datetime.now(UTC)
        await self._session.flush()

    async def schedule_retry(
        self,
        event: Outbox,
        attempts: int,
        next_retry_at: datetime,
    ) -> None:
        event.retry_count = attempts
        event.backoff_delay = next_retry_at
        await self._session.flush()

    async def mark_failed(self, event: Outbox, attempts: int) -> None:
        event.retry_count = attempts
        event.status = OutboxStatus.FAILED
        await self._session.flush()
