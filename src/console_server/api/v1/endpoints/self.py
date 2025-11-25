from typing import cast
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi import APIRouter, Depends, Body

from console_server.core.constants import SELF_PATH
from console_server.db import database
from console_server.model.rbac import User
from console_server.schema.common import SuccessResponse
from console_server.schema.permission import PermissionResponse
from console_server.schema.role import UserRoleResponse
from console_server.schema.user import (
    CurrentUserResponse,
    UpdateUserRequest,
)

from console_server.utils.auth import get_current_user

router = APIRouter(prefix=f"/{SELF_PATH}", tags=[SELF_PATH])


# 获取当前用户信息
@router.get(
    "/current",
    summary="获取当前用户信息",
    description="使用 JWT token 获取当前登录用户的信息",
    response_model=CurrentUserResponse,
)
async def read_users_me(current_user: User = Depends(get_current_user)):
    return CurrentUserResponse(
        id=cast(int, current_user.id),
        name=cast(str, current_user.name),
        email=cast(str, current_user.email),
        description=cast(str, current_user.description),
        is_active=cast(bool, current_user.is_active),
        is_deletable=cast(bool, current_user.is_deletable),
        is_editable=cast(bool, current_user.is_editable),
        roles=[
            UserRoleResponse(
                id=cast(int, role.id),
                name=cast(str, role.name),
                display_name=cast(str, role.display_name),
            )
            for role in current_user.roles
        ],
        permissions=[
            PermissionResponse(
                id=cast(int, perm.id),
                name=cast(str, perm.name),
                display_name=cast(str, perm.display_name),
            )
            for perm in current_user.permissions
        ],
    )


# 更新当前用户的名称和描述
@router.put(
    "/update-current",
    summary="更新当前用户信息",
    description="更新当前登录用户的名称和描述",
    response_model=SuccessResponse,
)
async def update_current_user(
    user_request: UpdateUserRequest = Body(),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(database.get_db),
):
    name = user_request.name
    description = user_request.description
    await db.execute(
        update(User)
        .where(User.id == cast(int, current_user.id))
        .values(name=name, description=description)
    )
    await db.commit()
    return SuccessResponse()
