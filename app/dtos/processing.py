from dataclasses import dataclass
from typing import Any
from uuid import UUID

from app.common.enums import ProcessingState


@dataclass(frozen=True, slots=True, kw_only=True)
class PaymentProcessingResult:
    payment_id: UUID
    state: ProcessingState
    webhook_url: str | None = None
    webhook_payload: dict[str, Any] | None = None
