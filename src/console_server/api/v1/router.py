from fastapi import APIRouter

from .endpoints import (
    chat,
    self,
    table_list,
    user,
    role,
    permission,
)

v1_router = APIRouter(prefix="/v1")
v1_router.include_router(chat.router)
v1_router.include_router(table_list.router)
v1_router.include_router(self.router)
v1_router.include_router(user.router)
v1_router.include_router(role.router)
v1_router.include_router(permission.router)
