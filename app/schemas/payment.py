from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from app.common.enums import Currency, PaymentStatus


class BaseSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class PaymentCreateSchema(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "amount": "100.50",
                    "currency": "USD",
                    "description": "Order #42",
                    "metadata": {"order_id": "42", "source": "web"},
                    "webhook_url": "https://example.com/hooks/payments",
                }
            ]
        }
    )

    amount: Annotated[
        Decimal,
        Field(gt=0, max_digits=12, decimal_places=2, examples=["100.50"]),
    ]
    currency: Currency
    description: Annotated[
        str | None,
        Field(default=None, max_length=255, examples=["Order #42"]),
    ]
    metadata: Annotated[
        dict[str, Any],
        Field(default_factory=dict, examples=[{"order_id": "42"}]),
    ]
    webhook_url: Annotated[
        HttpUrl | None,
        Field(default=None, examples=["https://example.com/hooks/payments"]),
    ]


class PaymentCreateResponseSchema(BaseSchema):
    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "examples": [
                {
                    "payment_id": "9c3f3b1a-2b8e-4d6a-9c1f-1a2b3c4d5e6f",
                    "status": "pending",
                    "created_at": "2026-10-01T12:00:00Z",
                }
            ]
        },
    )

    payment_id: UUID
    status: PaymentStatus
    created_at: datetime


class PaymentResponseSchema(BaseSchema):
    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "examples": [
                {
                    "payment_id": "9c3f3b1a-2b8e-4d6a-9c1f-1a2b3c4d5e6f",
                    "amount": "100.50",
                    "currency": "USD",
                    "description": "Order #42",
                    "metadata": {"order_id": "42"},
                    "status": "succeeded",
                    "webhook_url": "https://example.com/hooks/payments",
                    "created_at": "2026-10-01T12:00:00Z",
                    "processed_at": "2026-10-01T12:00:04Z",
                }
            ]
        },
    )

    payment_id: UUID
    amount: Annotated[
        Decimal,
        Field(gt=0, max_digits=12, decimal_places=2),
    ]
    currency: Currency
    description: Annotated[str | None, Field(default=None, max_length=255)]
    metadata: dict[str, Any] = Field(default_factory=dict)
    status: PaymentStatus
    webhook_url: HttpUrl | None = None
    created_at: datetime
    processed_at: datetime | None = None
