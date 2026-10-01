import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.broker.producer import publish_payment_new, publish_payment_to_dlq
from app.common.retry import attempts_exhausted, backoff_delay
from app.db.uow import UnitOfWork
from app.dtos.outbox import OutboxDispatchResult

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
                await publish_payment_new(
                    event.payload,
                    message_id=str(event.id),
                )
                await self._uow.outbox.mark_published(event)
                sent += 1
                await self._uow.commit()
            except Exception:
                logger.exception("Outbox: publish failed for event %s", event.id)
                attempts = event.retry_count + 1

                if attempts_exhausted(attempts=attempts):
                    await self._uow.outbox.mark_failed(event, attempts=attempts)
                    failed += 1
                    try:
                        await publish_payment_to_dlq(
                            event.payload,
                            message_id=str(event.id),
                        )
                    except Exception:
                        logger.exception("Outbox: DLQ publish also failed for event %s", event.id)
                else:
                    await self._uow.outbox.schedule_retry(
                        event,
                        attempts=attempts,
                        next_retry_at=backoff_delay(attempts=attempts),
                    )
                await self._uow.commit()

        return OutboxDispatchResult(selected=len(events), sent=sent, failed=failed)
