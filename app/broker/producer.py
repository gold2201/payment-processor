import logging
from typing import Any

from app.broker.declarations import (
    broker,
    payments_dlx,
    payments_exchange,
    retry_queue_name,
)
from app.common.headers import RETRY_COUNT_HEADER
from app.core.settings import settings

logger = logging.getLogger(__name__)


async def publish_payment_new(
    message: dict[str, Any],
    *,
    message_id: str | None = None,
) -> None:
    await broker.publish(
        message=message,
        exchange=payments_exchange,
        routing_key=settings.PAYMENTS_NEW_QUEUE,
        message_id=message_id,
        persist=True,
        mandatory=True,
    )
    logger.debug("Published to %s: message_id=%s", settings.PAYMENTS_NEW_QUEUE, message_id)


async def publish_payment_to_dlq(
    message: dict[str, Any],
    *,
    message_id: str | None = None,
) -> None:
    await broker.publish(
        message=message,
        exchange=payments_dlx,
        routing_key=settings.PAYMENTS_DLQ,
        message_id=message_id,
        persist=True,
        mandatory=True,
    )
    logger.warning(
        "Published to DLQ %s: message_id=%s reason=%s",
        settings.PAYMENTS_DLQ,
        message_id,
        message.get("reason", "<unknown>"),
    )


async def publish_payment_retry(
    message: dict[str, Any],
    *,
    failed_attempts: int,
    message_id: str | None = None,
) -> None:
    await broker.publish(
        message=message,
        exchange=payments_dlx,
        routing_key=retry_queue_name(failed_attempts),
        headers={RETRY_COUNT_HEADER: failed_attempts},
        message_id=message_id,
        persist=True,
        mandatory=True,
    )
