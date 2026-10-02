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
        sent = 0
        failed = 0

        events = await self._uow.outbox.get_ready_for_dispatch(limit=limit)

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

            await self._uow.outbox.mark_published(event)
            sent += 1

        if events:
            await self._uow.commit()

        return OutboxDispatchResult(selected=len(events), sent=sent, failed=failed)

    async def _handle_publish_failure(self, event: Outbox, exc: Exception) -> bool:
        attempts = event.retry_count + 1

        if not attempts_exhausted(attempts=attempts):
            await self._uow.outbox.schedule_retry(
                event,
                attempts=attempts,
                next_retry_at=backoff_delay(attempts=attempts),
            )
            return False

        await self._uow.outbox.mark_failed(event, attempts=attempts)
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
