import logging
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.settings import settings

logger = logging.getLogger(__name__)

engine = create_async_engine(
    settings.database_url,
    echo=settings.DEBUG,
    pool_pre_ping=True,
)

async_session_factory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_session() -> AsyncGenerator[AsyncSession]:
    async with async_session_factory() as session:
        try:
            yield session
        except Exception:
            logger.exception("Session rollback due to unhandled exception")
            raise
