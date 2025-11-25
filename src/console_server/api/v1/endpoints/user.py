from typing import List, cast
from sqlalchemy import select, update
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi import APIRouter, Depends, HTTPException, status, Body

from console_server.core.constants import (
    USER_PATH,
    USER_PUT_API,
    USER_POST_API,
    USER_GET_API,
    USER_DELETE_API,
)

from console_server.db import database
from console_server.model.rbac import User, Role
from console_server.schema.common import SuccessResponse
from console_server.schema.user import UserResponse, DisableUserRequest
from console_server.schema.role import (
    AssignRolesRequest,
    RemoveRolesRequest,
    UserRoleResponse,
)
from console_server.utils.auth import require_permission


router = APIRouter(prefix=f"/{USER_PATH}", tags=[USER_PATH])


# 禁用/启用某个用户
@router.put(
    "/{user_id}/disable",
    summary="禁用某个用户",
    description="禁用某个用户",
    response_model=SuccessResponse,
)
async def disable_user(
    user_id: int,
    role_request: DisableUserRequest = Body(),
    current_user: User = Depends(require_permission(USER_PATH, USER_PUT_API)),
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
    is_active = role_request.is_active
    await db.execute(update(User).where(User.id == user_id).values(is_active=is_active))
    await db.commit()
    return SuccessResponse()


# 为用户批量分配角色
@router.post(
    "/{user_id}/assign-roles",
    summary="为用户分配多个角色（通过ID）",
    description="根据角色ID列表为特定用户分配角色权限。",
    response_model=SuccessResponse,
)
async def assign_role_to_user(
    user_id: int,
    role_request: AssignRolesRequest = Body(default=AssignRolesRequest(role_ids=[])),
    current_user: User = Depends(require_permission(USER_PATH, USER_POST_API)),
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
    print(f"Found roles: {found_role_ids}")
    missing_role_ids = {
        int(role_id) for role_id in role_request.role_ids
    } - found_role_ids
    print(f"Missing roles: {missing_role_ids}")
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

    return SuccessResponse()


# 获取某个用户的角色
@router.get(
    "/{user_id}/roles",
    summary="获取某个用户的角色",
    description="获取某个用户的角色列表（需要登录）",
    response_model=List[UserRoleResponse],
)
async def get_user_roles(
    user_id: int,
    current_user: User = Depends(require_permission(USER_PATH, USER_GET_API)),
    db: AsyncSession = Depends(database.get_db),
):
    # 获取用户信息
    result = await db.execute(
        select(User).where(User.id == user_id).options(selectinload(User.roles))
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="User not found"
        )
    return [
        UserRoleResponse(
            id=cast(int, role.id), name=str(role.name), display_name=role.display_name
        )
        for role in user.roles
    ]


# 批量删除某个用户的角色
@router.post(
    "/{user_id}/remove-roles",
    summary="批量删除某个用户的角色",
    description="批量删除某个用户的角色（需要登录）",
    response_model=SuccessResponse,
)
async def delete_user_roles(
    user_id: int,
    role_request: RemoveRolesRequest = Body(default=RemoveRolesRequest(role_ids=[])),
    current_user: User = Depends(require_permission(USER_PATH, USER_DELETE_API)),
    db: AsyncSession = Depends(database.get_db),
):
    # 获取用户信息
    result = await db.execute(
        select(User).where(User.id == user_id).options(selectinload(User.roles))
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="User not found"
        )
    role_ids = role_request.role_ids
    # 获取所有待删除的角色对象
    stmt = select(Role).where(Role.id.in_(role_ids))
    result = await db.execute(stmt)
    roles_to_delete = result.scalars().all()
    if not roles_to_delete:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Role not found"
        )
    # 验证待删除的角色是否属于该用户
    existing_role_ids = {r.id for r in user.roles}
    missing_role_ids = set(role_ids) - existing_role_ids
    if missing_role_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"User does not have roles: {list(missing_role_ids)}",
        )

    # 根据 name 为 user 获取普通角色
    user_role_result = await db.execute(select(Role).where(Role.name == "user"))
    # 如果是 user 角色，则禁止删除
    user_role = user_role_result.scalar_one_or_none()
    if user_role and user_role.id in role_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot remove 'user' role from user",
        )
    # 删除角色并提交事务
    user.roles = [role for role in user.roles if role.id not in role_ids]
    db.add(user)
    await db.commit()
    return SuccessResponse()
