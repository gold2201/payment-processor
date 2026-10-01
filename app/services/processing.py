import asyncio
import logging
import random
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums import PaymentStatus, ProcessingState
from app.db.uow import UnitOfWork
from app.dtos.processing import PaymentProcessingResult

logger = logging.getLogger(__name__)

MIN_DELAY_SECONDS = 2.0
MAX_DELAY_SECONDS = 5.0
SUCCESS_RATE = 0.9


class PaymentProcessingService:
    def __init__(self, session: AsyncSession) -> None:
        self._uow = UnitOfWork(session)

    async def process_payment_created(self, payment_id: UUID) -> PaymentProcessingResult:
        payment = await self._uow.payments.get_by_id_for_update(payment_id)

        if payment is None:
            logger.warning("Payment not found: id=%s", payment_id)
            return PaymentProcessingResult(
                payment_id=payment_id,
                state=ProcessingState.NOT_FOUND,
            )

        if payment.processed_at is not None:
            logger.info("Payment already processed: id=%s", payment_id)
            return PaymentProcessingResult(
                payment_id=payment.payment_id,
                state=ProcessingState.ALREADY_PROCESSED,
                webhook_url=payment.webhook_url,
                webhook_payload=_build_webhook_payload(
                    payment_id=payment.payment_id,
                    status=str(payment.status),
                    processed_at=payment.processed_at,
                    amount=str(payment.amount),
                    currency=str(payment.currency),
                ),
            )

        await asyncio.sleep(random.uniform(MIN_DELAY_SECONDS, MAX_DELAY_SECONDS))  # noqa: S311

        new_status = PaymentStatus.SUCCEEDED if random.random() < SUCCESS_RATE else PaymentStatus.FAILED  # noqa: S311
        await self._uow.payments.update_status(payment, new_status)
        await self._uow.commit()

        logger.info("Payment processed: id=%s status=%s", payment.payment_id, str(new_status))

        return PaymentProcessingResult(
            payment_id=payment.payment_id,
            state=ProcessingState.PROCESSED,
            webhook_url=payment.webhook_url,
            webhook_payload=_build_webhook_payload(
                payment_id=payment.payment_id,
                status=str(new_status),
                processed_at=payment.processed_at,
                amount=str(payment.amount),
                currency=str(payment.currency),
            ),
        )


def _build_webhook_payload(
    *,
    payment_id: UUID,
    status: str,
    processed_at: datetime | None,
    amount: str,
    currency: str,
) -> dict[str, Any]:
    return {
        "payment_id": str(payment_id),
        "status": status,
        "amount": amount,
        "currency": currency,
        "processed_at": processed_at.isoformat() if processed_at else None,
    }
