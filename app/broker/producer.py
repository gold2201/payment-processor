from typing import Any

from app.broker.declarations import (
    broker,
    payments_dlx,
    payments_exchange,
)
from app.core.settings import settings


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
