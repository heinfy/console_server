from fastapi import APIRouter

from .endpoints import (
    user,
    role,
)

v1_router = APIRouter(prefix="/v1")
v1_router.include_router(user.router)
v1_router.include_router(role.router)
