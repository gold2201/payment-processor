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
from app.broker.producer import publish_payment_to_dlq
from app.common.enums import DeliveryStatus, ProcessingState
from app.common.headers import parse_retry_count
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
    payment_id_raw = message.get("payment_id")
    if not payment_id_raw:
        logger.warning("Skip message without payment_id: %s", message)
        try:
            await publish_payment_to_dlq({"reason": "missing_payment_id", "payload": message})
            await msg.ack()
        except Exception:
            await msg.nack(requeue=False)
        return

    try:
        payment_id = UUID(payment_id_raw)
    except ValueError:
        logger.warning("Skip message with invalid payment_id: %s", message)
        try:
            await publish_payment_to_dlq(
                {"reason": "invalid_payment_id", "payload": message},
                message_id=str(payment_id_raw),
            )
            await msg.ack()
        except Exception:
            await msg.nack(requeue=False)
        return

    retries = parse_retry_count(msg, settings.PAYMENTS_NEW_QUEUE)
    if retries >= settings.MAX_CONSUMER_RETRIES:
        logger.error(
            "Consumer retries exhausted for payment_id=%s (retries=%s)",
            payment_id,
            retries,
        )
        try:
            await publish_payment_to_dlq(
                {
                    "reason": "consumer_retries_exhausted",
                    "payment_id": str(payment_id),
                    "payload": message,
                },
                message_id=str(payment_id),
            )
            await msg.ack()
        except Exception:
            await msg.nack(requeue=False)
        return

    try:
        async with async_session_factory() as session:
            result = await PaymentProcessingService(session).process_payment_created(
                payment_id=payment_id,
            )

        if result.state == ProcessingState.NOT_FOUND:
            logger.warning("Payment not found, ack and drop: id=%s", payment_id)
            await msg.ack()
            return

        delivery_status = await webhook_sender.send(
            payment_id=result.payment_id,
            target_url=result.webhook_url,
            payload=result.webhook_payload or {},
        )

        if delivery_status == DeliveryStatus.DLQ_PUBLISH_FAILED:
            logger.error("Webhook DLQ publish failed, requeue: id=%s", result.payment_id)
            await msg.nack(requeue=False)
            return

        await msg.ack()

    except Exception:
        logger.exception("Processing error for payment_id=%s", payment_id)
        await msg.nack(requeue=False)


@app.after_startup
async def on_startup() -> None:
    await declare_topology()
    logger.info("Consumer started")


if __name__ == "__main__":
    setup_logging()
    asyncio.run(app.run())
