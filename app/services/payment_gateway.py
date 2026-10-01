import asyncio
import random

from app.common.enums import PaymentStatus


class PaymentGatewayEmulator:
    def __init__(
        self,
        min_delay: float = 2.0,
        max_delay: float = 5.0,
        success_rate: float = 0.9,
    ) -> None:
        self._min_delay = min_delay
        self._max_delay = max_delay
        self._success_rate = success_rate

    async def process(self) -> PaymentStatus:
        await asyncio.sleep(random.uniform(self._min_delay, self._max_delay))  # noqa: S311
        if random.random() < self._success_rate:  # noqa: S311
            return PaymentStatus.SUCCEEDED
        return PaymentStatus.FAILED
