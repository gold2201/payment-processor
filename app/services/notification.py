from abc import ABC, abstractmethod
from typing import Any
from uuid import UUID

from app.common.enums import DeliveryStatus


class NotificationSender(ABC):
    @abstractmethod
    async def send(
        self,
        payment_id: UUID,
        target_url: str | None,
        payload: dict[str, Any],
    ) -> DeliveryStatus: ...
