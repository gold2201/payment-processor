from fastapi import FastAPI

from app.api.router import api_router
from app.core.settings import settings

app = FastAPI(
    title=settings.APP_NAME,
    version="0.1.0",
    debug=settings.DEBUG,
)

app.include_router(api_router)


@app.get("/health", tags=["system"])
async def health() -> dict[str, str]:
    return {"status": "ok"}
