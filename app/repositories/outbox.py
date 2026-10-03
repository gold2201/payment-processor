from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import CursorResult, select, update

from app.common.enums import OutboxStatus
from app.models.outbox import Outbox
from app.repositories.base import BaseRepository

STUCK_TTL_SECONDS = 300


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

    async def claim_pending_batch(self, limit: int) -> list[Outbox]:
        now = datetime.now(UTC)
        claimed_ids = (
            select(Outbox.id)
            .where(Outbox.status == OutboxStatus.PENDING)
            .where((Outbox.backoff_delay.is_(None)) | (Outbox.backoff_delay <= now))
            .order_by(Outbox.created_at.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
            .cte("claimed_ids")
        )
        stmt = (
            update(Outbox)
            .where(Outbox.id.in_(select(claimed_ids.c.id)))
            .values(
                status=OutboxStatus.PROCESSING,
                processing_started_at=now,
            )
            .returning(Outbox)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def reclaim_stuck(self, ttl_seconds: int = STUCK_TTL_SECONDS) -> int:
        threshold = datetime.now(UTC) - timedelta(seconds=ttl_seconds)
        stmt = (
            update(Outbox)
            .where(Outbox.status == OutboxStatus.PROCESSING)
            .where(Outbox.processing_started_at < threshold)
            .values(
                status=OutboxStatus.PENDING,
                processing_started_at=None,
            )
        )
        result: CursorResult[Any] = await self._session.execute(stmt)  # type: ignore[assignment]
        return result.rowcount or 0

    async def mark_published_by_id(self, event_id: UUID) -> None:
        stmt = (
            update(Outbox)
            .where(Outbox.id == event_id)
            .values(
                status=OutboxStatus.PUBLISHED,
                published_at=datetime.now(UTC),
                processing_started_at=None,
            )
        )
        await self._session.execute(stmt)

    async def schedule_retry_by_id(
        self,
        event_id: UUID,
        *,
        attempts: int,
        next_retry_at: datetime,
    ) -> None:
        stmt = (
            update(Outbox)
            .where(Outbox.id == event_id)
            .values(
                retry_count=attempts,
                backoff_delay=next_retry_at,
                status=OutboxStatus.PENDING,
                processing_started_at=None,
            )
        )
        await self._session.execute(stmt)

    async def mark_failed_by_id(self, event_id: UUID, *, attempts: int) -> None:
        stmt = (
            update(Outbox)
            .where(Outbox.id == event_id)
            .values(
                retry_count=attempts,
                status=OutboxStatus.FAILED,
                processing_started_at=None,
            )
        )
        await self._session.execute(stmt)
