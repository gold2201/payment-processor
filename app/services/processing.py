import asyncio
import logging
import random
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums import PaymentStatus, ProcessingState
from app.db.uow import UnitOfWork
from app.dtos.processing import PaymentProcessingResult
from app.models.payment import Payment

logger = logging.getLogger(__name__)

MIN_DELAY_SECONDS = 2.0
MAX_DELAY_SECONDS = 5.0
SUCCESS_RATE = 0.9


async def _simulate_gateway() -> PaymentStatus:
    await asyncio.sleep(random.uniform(MIN_DELAY_SECONDS, MAX_DELAY_SECONDS))  # noqa: S311
    if random.random() < SUCCESS_RATE:  # noqa: S311
        return PaymentStatus.SUCCEEDED
    return PaymentStatus.FAILED


class PaymentProcessingService:
    def __init__(self, session: AsyncSession) -> None:
        self._uow = UnitOfWork(session)

    async def process_payment_created(self, payment_id: UUID) -> PaymentProcessingResult:
        async with self._uow:
            payment = await self._uow.payments.get_by_id_for_update(payment_id)

            if payment is None:
                logger.warning("Payment not found: id=%s", payment_id)
                await self._uow.commit()
                return PaymentProcessingResult(payment_id=payment_id, state=ProcessingState.NOT_FOUND)

            if payment.processed_at is not None:
                logger.info("Payment already processed: id=%s", payment_id)
                result = _build_result(payment, ProcessingState.ALREADY_PROCESSED)
                await self._uow.commit()
                return result

            new_status = await _simulate_gateway()

            await self._uow.payments.update_status(payment, new_status)
            result = _build_result(payment, ProcessingState.PROCESSED)
            await self._uow.commit()

        logger.info("Payment processed: id=%s status=%s", payment_id, str(new_status))
        return result


def _build_result(payment: Payment, state: ProcessingState) -> PaymentProcessingResult:
    return PaymentProcessingResult(
        payment_id=payment.payment_id,
        state=state,
        webhook_url=payment.webhook_url,
        webhook_payload=_build_webhook_payload(payment),
    )


def _build_webhook_payload(payment: Payment) -> dict[str, Any]:
    return {
        "payment_id": str(payment.payment_id),
        "status": str(payment.status),
        "amount": str(payment.amount),
        "currency": str(payment.currency),
        "processed_at": payment.processed_at.isoformat() if payment.processed_at else None,
    }
