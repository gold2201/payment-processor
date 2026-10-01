import aio_pika
from aio_pika import ExchangeType
from faststream.rabbit import RabbitBroker, RabbitExchange, RabbitQueue

from app.core.settings import settings

broker = RabbitBroker(settings.rabbit_url)

payments_exchange = RabbitExchange(
    name=settings.PAYMENTS_EXCHANGE,
    durable=True,
)

payments_dlx = RabbitExchange(
    name=settings.PAYMENTS_DLX,
    durable=True,
)

payments_new_queue = RabbitQueue(
    name=settings.PAYMENTS_NEW_QUEUE,
    durable=True,
    routing_key=settings.PAYMENTS_NEW_QUEUE,
    arguments={
        "x-dead-letter-exchange": settings.PAYMENTS_DLX,
        "x-dead-letter-routing-key": settings.PAYMENTS_RETRY_QUEUE,
    },
)

payments_retry_queue = RabbitQueue(
    name=settings.PAYMENTS_RETRY_QUEUE,
    durable=True,
    routing_key=settings.PAYMENTS_RETRY_QUEUE,
    arguments={
        "x-message-ttl": settings.PAYMENTS_RETRY_DELAY_MS,
        "x-dead-letter-exchange": settings.PAYMENTS_EXCHANGE,
        "x-dead-letter-routing-key": settings.PAYMENTS_NEW_QUEUE,
    },
)

payments_dlq_queue = RabbitQueue(
    name=settings.PAYMENTS_DLQ,
    durable=True,
    routing_key=settings.PAYMENTS_DLQ,
)


async def declare_topology() -> None:
    connection: aio_pika.RobustConnection = await aio_pika.connect_robust(settings.rabbit_url)
    try:
        channel = await connection.channel()

        payments_ex = await channel.declare_exchange(
            settings.PAYMENTS_EXCHANGE,
            ExchangeType.DIRECT,
            durable=True,
        )
        payments_dlx_ex = await channel.declare_exchange(
            settings.PAYMENTS_DLX,
            ExchangeType.DIRECT,
            durable=True,
        )

        new_q = await channel.declare_queue(
            settings.PAYMENTS_NEW_QUEUE,
            durable=True,
            arguments={
                "x-dead-letter-exchange": settings.PAYMENTS_DLX,
                "x-dead-letter-routing-key": settings.PAYMENTS_RETRY_QUEUE,
            },
        )
        retry_q = await channel.declare_queue(
            settings.PAYMENTS_RETRY_QUEUE,
            durable=True,
            arguments={
                "x-message-ttl": settings.PAYMENTS_RETRY_DELAY_MS,
                "x-dead-letter-exchange": settings.PAYMENTS_EXCHANGE,
                "x-dead-letter-routing-key": settings.PAYMENTS_NEW_QUEUE,
            },
        )
        dlq = await channel.declare_queue(
            settings.PAYMENTS_DLQ,
            durable=True,
        )

        await new_q.bind(payments_ex, routing_key=settings.PAYMENTS_NEW_QUEUE)
        await retry_q.bind(payments_dlx_ex, routing_key=settings.PAYMENTS_RETRY_QUEUE)
        await dlq.bind(payments_dlx_ex, routing_key=settings.PAYMENTS_DLQ)
    finally:
        await connection.close()
