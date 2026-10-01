from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.uow import UnitOfWork
from app.dtos.mappers import payment_to_dto
from app.dtos.payment import PaymentDTO
from app.schemas.payment import PaymentCreateSchema


class PaymentService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._uow = UnitOfWork(session)

    async def get_payment(self, payment_id: UUID) -> PaymentDTO | None:
        payment = await self._uow.payments.get(payment_id)
        if payment is None:
            return None
        return payment_to_dto(payment)

    async def create_payment(
        self,
        data: PaymentCreateSchema,
        idempotency_key: str,
    ) -> PaymentDTO:
        existing = await self._uow.payments.get_by_idempotency_key(idempotency_key)
        if existing is not None:
            return payment_to_dto(existing)

        try:
            async with self._uow:
                payment = await self._uow.payments.create(
                    amount=data.amount,
                    currency=data.currency,
                    description=data.description,
                    metadata=data.metadata,
                    idempotency_key=idempotency_key,
                    webhook_url=str(data.webhook_url) if data.webhook_url else None,
                )
                await self._uow.outbox.create_payment_created(payment.payment_id)
                await self._uow.commit()
        except IntegrityError:
            await self._session.rollback()
            existing = await self._uow.payments.get_by_idempotency_key(idempotency_key)
            if existing is None:
                raise
            return payment_to_dto(existing)

        return payment_to_dto(payment)
