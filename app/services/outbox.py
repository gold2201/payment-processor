import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.broker.producer import publish_payment_new, publish_payment_to_dlq
from app.common.retry import attempts_exhausted, backoff_delay
from app.core.settings import settings
from app.db.uow import UnitOfWork
from app.dtos.outbox import OutboxDispatchResult
from app.models.outbox import Outbox

logger = logging.getLogger(__name__)


class OutboxService:
    def __init__(self, session: AsyncSession) -> None:
        self._uow = UnitOfWork(session)

    async def dispatch_pending(self, limit: int = 100) -> OutboxDispatchResult:
        async with self._uow:
            reclaimed = await self._uow.outbox.reclaim_stuck()
            if reclaimed > 0:
                logger.warning("Outbox: reclaimed %s stuck events", reclaimed)

            events = await self._uow.outbox.claim_pending_batch(limit=limit)
            await self._uow.commit()

        if not events:
            return OutboxDispatchResult(selected=0, sent=0, failed=0)

        sent = 0
        failed = 0

        for event in events:
            try:
                async with asyncio.timeout(settings.OUTBOX_PUBLISH_TIMEOUT_SECONDS):
                    await publish_payment_new(
                        event.payload,
                        message_id=str(event.id),
                    )
            except Exception as exc:
                logger.exception("Outbox: publish failed for event %s", event.id)
                if await self._handle_publish_failure(event, exc):
                    failed += 1
                continue

            try:
                async with self._uow:
                    await self._uow.outbox.mark_published_by_id(event.id)
                    await self._uow.commit()
                sent += 1
            except Exception:
                logger.exception("Outbox: mark_published failed for event %s", event.id)

        return OutboxDispatchResult(selected=len(events), sent=sent, failed=failed)

    async def _handle_publish_failure(self, event: Outbox, exc: Exception) -> bool:
        attempts = event.retry_count + 1

        try:
            async with self._uow:
                if not attempts_exhausted(attempts=attempts):
                    await self._uow.outbox.schedule_retry_by_id(
                        event.id,
                        attempts=attempts,
                        next_retry_at=backoff_delay(attempts=attempts),
                    )
                    await self._uow.commit()
                    return False

                await self._uow.outbox.mark_failed_by_id(event.id, attempts=attempts)
                await self._uow.commit()
        except Exception:
            logger.exception("Outbox: failed to persist failure state for event %s", event.id)

        try:
            async with asyncio.timeout(settings.OUTBOX_PUBLISH_TIMEOUT_SECONDS):
                await publish_payment_to_dlq(
                    {
                        "payment_id": event.payload.get("payment_id"),
                        "reason": f"outbox_publish_failed: {exc!r}",
                        "payload": event.payload,
                    },
                    message_id=str(event.id),
                )
        except Exception:
            logger.exception("Outbox: DLQ publish also failed for event %s", event.id)
        return True
