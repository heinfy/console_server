from fastapi import APIRouter

from .endpoints import (
    user,
    role,
    permission,
)

v1_router = APIRouter(prefix="/v1")
v1_router.include_router(user.router)
v1_router.include_router(role.router)
v1_router.include_router(permission.router)
