from fastapi import APIRouter

from .v1.router import v1_router
from .auth import auth_router

router = APIRouter()
router.include_router(v1_router)
router.include_router(auth_router)


@router.get(
    "/health",
    summary="测试",
    description="测试服务是否启动",
)
async def health(tag="服务测试"):
    return {"msg": "ok"}
