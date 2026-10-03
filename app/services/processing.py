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
            claimed = await self._uow.payments.claim_for_processing(payment_id)

            if claimed is None:
                current = await self._uow.payments.get(payment_id)
                await self._uow.commit()

                if current is None:
                    logger.warning("Payment not found: id=%s", payment_id)
                    return PaymentProcessingResult(
                        payment_id=payment_id,
                        state=ProcessingState.NOT_FOUND,
                    )

                logger.info(
                    "Payment already processed or in-flight: id=%s processed_at=%s",
                    payment_id,
                    current.processed_at,
                )
                return _build_result(current, ProcessingState.ALREADY_PROCESSED)

            await self._uow.commit()

        logger.info("Calling gateway: payment_id=%s", payment_id)
        new_status = await _simulate_gateway()

        async with self._uow:
            finalized = await self._uow.payments.finalize_processing(payment_id, new_status)
            await self._uow.commit()

        if finalized is None:
            logger.warning("Finalize lost race, another worker finalized: id=%s", payment_id)
            async with self._uow:
                current = await self._uow.payments.get(payment_id)
                await self._uow.commit()

            if current is None:
                return PaymentProcessingResult(
                    payment_id=payment_id,
                    state=ProcessingState.NOT_FOUND,
                )
            return _build_result(current, ProcessingState.ALREADY_PROCESSED)

        logger.info("Payment processed: id=%s status=%s", payment_id, str(new_status))
        return _build_result(finalized, ProcessingState.PROCESSED)


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
