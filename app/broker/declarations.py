from typing import Any, cast

import aio_pika
from aio_pika import ExchangeType
from faststream.rabbit import RabbitBroker, RabbitExchange, RabbitQueue

from app.core.settings import settings

broker = RabbitBroker(settings.rabbit_url)

payments_exchange = RabbitExchange(name=settings.PAYMENTS_EXCHANGE, durable=True)
payments_dlx = RabbitExchange(name=settings.PAYMENTS_DLX, durable=True)

NEW_QUEUE_ARGUMENTS: dict[str, str] = {
    "x-dead-letter-exchange": settings.PAYMENTS_DLX,
    "x-dead-letter-routing-key": settings.PAYMENTS_DLQ,
}

payments_new_queue = RabbitQueue(
    name=settings.PAYMENTS_NEW_QUEUE,
    durable=True,
    routing_key=settings.PAYMENTS_NEW_QUEUE,
    arguments=cast("Any", NEW_QUEUE_ARGUMENTS),
)


def retry_levels() -> range:
    return range(1, settings.MAX_CONSUMER_ATTEMPTS)


def retry_queue_name(level: int) -> str:
    return f"{settings.PAYMENTS_RETRY_QUEUE}.{level}"


def retry_delay_ms(level: int) -> int:
    return settings.PAYMENTS_RETRY_BASE_DELAY_MS * (1 << (level - 1))


async def declare_topology() -> None:
    connection = await aio_pika.connect_robust(settings.rabbit_url)
    try:
        channel = await connection.channel()

        payments_ex = await channel.declare_exchange(settings.PAYMENTS_EXCHANGE, ExchangeType.DIRECT, durable=True)
        dlx_ex = await channel.declare_exchange(settings.PAYMENTS_DLX, ExchangeType.DIRECT, durable=True)

        new_q = await channel.declare_queue(
            settings.PAYMENTS_NEW_QUEUE,
            durable=True,
            arguments=dict(NEW_QUEUE_ARGUMENTS),
        )
        await new_q.bind(payments_ex, routing_key=settings.PAYMENTS_NEW_QUEUE)

        for level in retry_levels():
            retry_q = await channel.declare_queue(
                retry_queue_name(level),
                durable=True,
                arguments={
                    "x-message-ttl": retry_delay_ms(level),
                    "x-dead-letter-exchange": settings.PAYMENTS_EXCHANGE,
                    "x-dead-letter-routing-key": settings.PAYMENTS_NEW_QUEUE,
                },
            )
            await retry_q.bind(dlx_ex, routing_key=retry_queue_name(level))

        dlq = await channel.declare_queue(settings.PAYMENTS_DLQ, durable=True)
        await dlq.bind(dlx_ex, routing_key=settings.PAYMENTS_DLQ)
    finally:
        await connection.close()
