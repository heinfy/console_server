from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi import APIRouter, Depends, HTTPException, status, Body
from sqlalchemy.orm import selectinload
from typing import cast

from console_server.db import database
from console_server.model.rbac import User, Role, Permission
from console_server.schema.role import RoleCreate, RoleResponse, RolePermissionResponse
from console_server.schema.permission import AssignPermissionsRequest


from console_server.utils.auth import get_current_user


router = APIRouter(prefix="/role", tags=["role"])


# 创建角色
@router.post(
    "/create",
    summary="创建角色",
    description="创建新角色",
    response_model=RoleResponse,
)
async def create_role(
    role: RoleCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(database.get_db),
):
    # 检查角色名称是否已存在
    result = await db.execute(select(Role).where(Role.name == role.name))
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Role name already exists"
        )
    # 创建新角色
    new_role = Role(
        name=role.name,
        display_name=role.display_name,
        description=role.description,
        is_active=role.is_active,
    )
    db.add(new_role)
    await db.commit()
    await db.refresh(new_role)
    return new_role


# 给角色分配权限
@router.post(
    "/{role_id}/assign-permissions",
    summary="为角色分配多个权限（通过ID）",
    description="根据权限ID列表为特定角色分配权限。",
    response_model=RolePermissionResponse,
)
async def assign_permissions_to_role(
    role_id: int,
    permission_request: AssignPermissionsRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(database.get_db),
):
    # 查询目标角色是否存在
    result = await db.execute(
        select(Role).where(Role.id == role_id).options(selectinload(Role.permissions))
    )
    role = result.scalar_one_or_none()
    if not role:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Role not found"
        )

    # 查询所有待分配的权限对象
    stmt = select(Permission).where(
        Permission.id.in_(permission_request.permission_ids)
    )
    result = await db.execute(stmt)
    permissions_to_assign = result.scalars().all()

    # 如果部分权限不存在，则抛出错误提示具体缺失项
    found_permission_ids = {cast(int, p.id) for p in permissions_to_assign}
    missing_permission_ids = (
        set(permission_request.permission_ids) - found_permission_ids
    )
    if missing_permission_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Permissions with IDs {list(missing_permission_ids)} not found",
        )

    # 过滤掉已存在的权限关系，防止重复添加
    existing_permission_ids = {cast(int, p.id) for p in role.permissions}
    new_permissions = [
        p
        for p in permissions_to_assign
        if cast(int, p.id) not in existing_permission_ids
    ]

    role.permissions.extend(new_permissions)
    db.add(role)
    await db.commit()
    await db.refresh(role)

    print(role.id)
    print(new_permissions)
    # 返回角色信息
    return {
        "role_id": role.id,
        "permission_ids": [p.id for p in role.permissions],
    }
