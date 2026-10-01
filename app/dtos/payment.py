from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from app.common.enums import Currency, PaymentStatus


@dataclass(frozen=True, slots=True, kw_only=True)
class PaymentDTO:
    payment_id: UUID
    amount: Decimal
    currency: Currency
    description: str | None
    metadata: dict[str, Any]
    status: PaymentStatus
    webhook_url: str | None
    created_at: datetime
    processed_at: datetime | None
