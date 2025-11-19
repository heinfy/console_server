from typing import cast
from sqlalchemy import select, func, update
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi import APIRouter, Depends, Query, Body

from console_server.db import database
from console_server.model.rbac import User
from console_server.schema.common import SuccessResponse
from console_server.schema.user import (
    UserResponse,
    CurrentUserResponse,
    UserListResponse,
    UpdateUserRequest,
)

from console_server.utils.auth import get_current_user
from console_server.core.config import settings


router = APIRouter(prefix="/self", tags=["self"])


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
        is_active=cast(bool, current_user.is_active),
        is_deletable=cast(bool, current_user.is_deletable),
        is_editable=cast(bool, current_user.is_editable),
        roles=[cast(str, role.name) for role in current_user.roles],
        permissions=[cast(str, perm.name) for perm in current_user.permissions],
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
    print(current_user)
    await db.execute(
        update(User)
        .where(User.id == cast(int, current_user.id))
        .values(name=name, description=description)
    )
    await db.commit()
    return SuccessResponse()


# 获取用户列表
@router.get(
    "/users",
    summary="获取用户列表",
    description="获取用户列表（需要登录，支持分页）",
    response_model=UserListResponse,
)
async def read_users(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(database.get_db),
    page: int = Query(1, ge=1, description="页码，从1开始"),
    page_size: int = Query(
        settings.DEFAULT_PAGE_SIZE,
        ge=1,
        le=settings.MAX_PAGE_SIZE,
        description="每页数量，最大100",
    ),
):
    """
    获取用户列表接口（支持分页）

    需要有效的 JWT token 才能访问。
    token 会在请求头中自动验证，如果 token 无效或已被撤销，将返回 401 错误。

    参数：
    - page: 页码，从1开始，默认为1
    - page_size: 每页返回的数量，默认为10，最大为100
    """
    # 计算偏移量
    offset = (page - 1) * page_size

    # 获取总数
    count_result = await db.execute(select(func.count()).select_from(User))
    total = count_result.scalar_one()

    # 获取分页数据
    # 预加载 roles，避免序列化时触发懒加载（async 环境中会触发 greenlet 错误）
    result = await db.execute(
        select(User).options(selectinload(User.roles)).offset(offset).limit(page_size)
    )
    users = result.scalars().all()

    # 计算总页数
    total_pages = (total + page_size - 1) // page_size

    # 将 User 模型转换为 UserResponse schema
    user_responses = [
        UserResponse(
            id=cast(int, user.id),
            name=cast(str, user.name),
            email=cast(str, user.email),
            roles=[cast(str, role.name) for role in user.roles],
        )
        for user in users
    ]

    return UserListResponse(
        items=user_responses,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )
