from fastapi import APIRouter, Depends

from app.api.deps import verify_api_key
from app.api.v1.payments import payments_router

v1_router = APIRouter(prefix="/v1", dependencies=[Depends(verify_api_key)])
v1_router.include_router(payments_router)
