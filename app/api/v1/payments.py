from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.schemas.payment import (
    PaymentCreateResponseSchema,
    PaymentCreateSchema,
    PaymentResponseSchema,
)
from app.services.payments import PaymentService

payments_router = APIRouter(prefix="/payments", tags=["payments"])


@payments_router.post(
    "",
    response_model=PaymentCreateResponseSchema,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Create a new payment",
    description=(
        "Creates a payment and enqueues it for async processing. "
        "Repeated calls with the same `Idempotency-Key` return the same payment."
    ),
)
async def create_payment(
    payload: PaymentCreateSchema,
    idempotency_key: Annotated[
        str,
        Header(
            alias="Idempotency-Key",
            min_length=1,
            max_length=255,
            description="Unique client-generated key to prevent duplicate payments.",
            examples=["550e8400-e29b-41d4-a716-446655440000"],
        ),
    ],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PaymentCreateResponseSchema:
    service = PaymentService(session)
    dto = await service.create_payment(
        data=payload,
        idempotency_key=idempotency_key,
    )
    return PaymentCreateResponseSchema.model_validate(dto, from_attributes=True)


@payments_router.get(
    "/{payment_id}",
    response_model=PaymentResponseSchema,
    summary="Get payment by id",
)
async def get_payment(
    payment_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PaymentResponseSchema:
    service = PaymentService(session)
    dto = await service.get_payment(payment_id)
    if dto is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Payment not found",
        )
    return PaymentResponseSchema.model_validate(dto, from_attributes=True)
