import asyncio
import logging
from typing import Any
from uuid import UUID

from faststream import FastStream
from faststream.rabbit import RabbitMessage

from app.broker import (
    broker,
    declare_topology,
    payments_exchange,
    payments_new_queue,
)
from app.broker.producer import publish_payment_retry, publish_payment_to_dlq
from app.common.enums import DeliveryStatus, ProcessingState
from app.common.headers import get_retry_count
from app.core.logging import setup_logging
from app.core.settings import settings
from app.db.session import async_session_factory
from app.services.notification import NotificationSender
from app.services.processing import PaymentProcessingService
from app.services.webhook import PaymentWebhookSender

logger = logging.getLogger(__name__)

webhook_sender: NotificationSender = PaymentWebhookSender()

app = FastStream(broker)


@broker.subscriber(payments_new_queue, payments_exchange)
async def handle_payment_created(message: dict[str, Any], msg: RabbitMessage) -> None:
    attempt = get_retry_count(msg) + 1

    payment_id = _parse_payment_id(message)
    if payment_id is None:
        logger.warning("Invalid or missing payment_id: %s", message)
        await _dead_letter(msg, {"reason": "invalid_payment_id", "payload": message})
        return

    try:
        async with async_session_factory() as session:
            result = await PaymentProcessingService(session).process_payment_created(
                payment_id=payment_id,
            )

        if result.state == ProcessingState.IN_PROGRESS:
            logger.warning(
                "Payment is still in progress, scheduling retry: id=%s (attempt %s)",
                payment_id,
                attempt,
            )
            await _retry_or_dead_letter(msg, message, payment_id=payment_id, attempt=attempt)
            return

        if result.state in (ProcessingState.NOT_FOUND, ProcessingState.ALREADY_PROCESSED):
            logger.info("Skip webhook: state=%s id=%s", result.state, payment_id)
            await msg.ack()
            return

        delivery_status = await webhook_sender.send(
            payment_id=result.payment_id,
            target_url=result.webhook_url,
            payload=result.webhook_payload or {},
        )

        if delivery_status == DeliveryStatus.DLQ_PUBLISH_FAILED:
            raise RuntimeError("webhook failed and DLQ publish failed")

        await msg.ack()

    except Exception:
        logger.exception("Processing error for payment_id=%s (attempt %s)", payment_id, attempt)
        await _retry_or_dead_letter(msg, message, payment_id=payment_id, attempt=attempt)


def _parse_payment_id(message: dict[str, Any]) -> UUID | None:
    raw = message.get("payment_id") if isinstance(message, dict) else None
    if not raw:
        return None
    try:
        return UUID(str(raw))
    except ValueError:
        return None


async def _retry_or_dead_letter(
    msg: RabbitMessage,
    message: dict[str, Any],
    *,
    payment_id: UUID,
    attempt: int,
) -> None:
    if attempt >= settings.MAX_CONSUMER_ATTEMPTS:
        logger.error("Attempts exhausted for payment_id=%s (attempts=%s)", payment_id, attempt)
        await _dead_letter(
            msg,
            {
                "reason": "consumer_retries_exhausted",
                "payment_id": str(payment_id),
                "payload": message,
            },
            message_id=str(payment_id),
        )
        return

    try:
        await publish_payment_retry(
            message,
            failed_attempts=attempt,
            message_id=str(payment_id),
        )
        await msg.ack()
    except Exception:
        logger.exception("Retry publish failed for payment_id=%s", payment_id)
        await msg.nack(requeue=False)


async def _dead_letter(
    msg: RabbitMessage,
    dlq_message: dict[str, Any],
    *,
    message_id: str | None = None,
) -> None:
    try:
        await publish_payment_to_dlq(dlq_message, message_id=message_id)
        await msg.ack()
    except Exception:
        logger.exception("DLQ publish failed")
        await msg.nack(requeue=False)


@app.after_startup
async def on_startup() -> None:
    await declare_topology()
    logger.info("Consumer started")


if __name__ == "__main__":
    setup_logging()
    asyncio.run(app.run())
