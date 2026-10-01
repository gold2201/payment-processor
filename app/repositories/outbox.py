from typing import Any
from uuid import UUID

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
