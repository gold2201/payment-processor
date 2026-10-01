import logging
from typing import Annotated

from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader

from app.core.settings import settings

logger = logging.getLogger(__name__)

_api_key_header = APIKeyHeader(name=settings.API_KEY_NAME, auto_error=False)


async def verify_api_key(
    api_key: Annotated[str | None, Security(_api_key_header)],
) -> None:
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Missing {settings.API_KEY_NAME} header",
        )
    if api_key != settings.API_KEY:
        logger.warning(
            "Auth failed: invalid %s value (prefix=%s)",
            settings.API_KEY_NAME,
            api_key[:4] + "..." if len(api_key) > 4 else "<short>",
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid API key",
        )
