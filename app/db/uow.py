from types import TracebackType

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.outbox import OutboxRepository
from app.repositories.payment import PaymentRepository


class UnitOfWork:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self.payments = PaymentRepository(session)
        self.outbox = OutboxRepository(session)

    async def __aenter__(self) -> "UnitOfWork":
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        if exc_type is not None:
            await self.rollback()

    async def commit(self) -> None:
        await self._session.commit()

    async def flush(self) -> None:
        await self._session.flush()

    async def rollback(self) -> None:
        await self._session.rollback()
