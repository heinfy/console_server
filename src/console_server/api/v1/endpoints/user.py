from typing import cast
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi import APIRouter, Depends, Query, HTTPException, status, Body

from console_server.db import database
from console_server.model.rbac import User, Role
from console_server.schema.user import UserResponse, UserListResponse
from console_server.schema.role import AssignRolesRequest
from console_server.utils.auth import get_current_user
from console_server.core.config import settings


router = APIRouter(prefix="/user", tags=["user"])


# 获取当前用户信息
@router.get(
    "/current",
    summary="获取当前用户信息",
    description="使用 JWT token 获取当前登录用户的信息",
    response_model=UserResponse,
)
async def read_users_me(current_user: User = Depends(get_current_user)):
    return UserResponse(
        id=cast(int, current_user.id),
        name=cast(str, current_user.name),
        email=cast(str, current_user.email),
        roles=[cast(str, role.name) for role in current_user.roles],
    )


# 给用户分配角色
@router.post(
    "/{user_id}/assign-roles",
    summary="为用户分配多个角色（通过ID）",
    description="根据角色ID列表为特定用户分配角色权限。",
    response_model=UserResponse,
)
async def assign_role_to_user(
    user_id: int,
    role_request: AssignRolesRequest = Body(default=AssignRolesRequest(role_ids=[])),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(database.get_db),
):
    # 查询目标用户是否存在
    result = await db.execute(
        select(User).where(User.id == user_id).options(selectinload(User.roles))
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="User not found"
        )

    # 查询所有待分配的角色对象
    stmt = select(Role).where(Role.id.in_(role_request.role_ids))
    result = await db.execute(stmt)
    roles_to_assign = result.scalars().all()

    # 如果部分角色不存在，则抛出错误提示具体缺失项
    found_role_ids = {cast(int, r.id) for r in roles_to_assign}
    missing_role_ids = set(role_request.role_ids) - found_role_ids
    if missing_role_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Roles with IDs {list(missing_role_ids)} not found",
        )

    # 过滤掉已存在的角色关系，防止重复添加
    existing_role_ids = {r.id for r in user.roles}
    new_roles = [r for r in roles_to_assign if r.id not in existing_role_ids]

    # 添加新角色并提交事务
    user.roles.extend(new_roles)
    db.add(user)
    await db.commit()
    await db.refresh(user)

    # 返回更新后用户信息
    return UserResponse(
        id=cast(int, user.id),
        name=str(user.name),
        email=str(user.email),
        roles=[str(role.name) for role in user.roles],
    )


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
