import asyncio
import logging

from app.broker import broker, declare_topology
from app.core.logging import setup_logging
from app.db.session import async_session_factory
from app.services.outbox import OutboxService

logger = logging.getLogger(__name__)

DISPATCH_BATCH_SIZE = 100
DISPATCH_POLL_INTERVAL_SECONDS = 1.0
DISPATCH_ERROR_BACKOFF_SECONDS = 3.0


async def run() -> None:
    async with broker:
        await declare_topology()
        logger.info("Outbox dispatcher started")

        while True:
            try:
                async with async_session_factory() as session:
                    stats = await OutboxService(session).dispatch_pending(
                        limit=DISPATCH_BATCH_SIZE,
                    )
                    if stats.selected > 0:
                        logger.info(
                            "Outbox dispatch: selected=%s sent=%s failed=%s",
                            stats.selected,
                            stats.sent,
                            stats.failed,
                        )
                await asyncio.sleep(DISPATCH_POLL_INTERVAL_SECONDS)
            except Exception:
                logger.exception("Outbox: dispatcher iteration failed")
                await asyncio.sleep(DISPATCH_ERROR_BACKOFF_SECONDS)


if __name__ == "__main__":
    setup_logging()
    asyncio.run(run())
