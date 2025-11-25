from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi import APIRouter, Depends, HTTPException, status

from console_server.core.constants import (
    PERMISSION_PATH,
    PERMISSION_POST_API,
    PERMISSION_PUT_API,
)
from console_server.db import database
from console_server.model.rbac import User, Permission
from console_server.schema.common import SuccessResponse
from console_server.schema.permission import PermissionResponse, PermissionCreate
from console_server.utils.auth import get_current_user, require_permission


router = APIRouter(prefix=f"/{PERMISSION_PATH}", tags=[PERMISSION_PATH])


# 创建权限
@router.post(
    "/create",
    summary="创建权限",
    description="创建新权限",
    response_model=PermissionResponse,
)
async def create_permission(
    permission: PermissionCreate,
    current_user: User = Depends(
        require_permission(PERMISSION_PATH, PERMISSION_POST_API)
    ),
    db: AsyncSession = Depends(database.get_db),
):
    # 检查权限名称是否已存在
    result = await db.execute(
        select(Permission).where(Permission.name == permission.name)
    )
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Permission name already exists",
        )
    # 创建新权限
    new_permission = Permission(
        name=permission.name,
        display_name=permission.display_name,
        description=permission.description,
    )
    db.add(new_permission)
    await db.commit()
    await db.refresh(new_permission)
    return new_permission


# 更新权限
@router.put(
    "/{permission_id}/update",
    summary="更新权限",
    description="更新某个权限",
    response_model=SuccessResponse,
)
async def update_permission(
    permission_id: int,
    permission: PermissionCreate,
    current_user: User = Depends(
        require_permission(PERMISSION_PATH, PERMISSION_PUT_API)
    ),
    db: AsyncSession = Depends(database.get_db),
):
    result = await db.execute(select(Permission).where(Permission.id == permission_id))
    existing_permission = result.scalar_one_or_none()
    if not existing_permission:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Permission not found"
        )
    # 更新权限信息
    existing_permission.name = permission.name  # type: ignore
    existing_permission.display_name = permission.display_name  # type: ignore
    existing_permission.description = permission.description  # type: ignore
    db.add(existing_permission)
    await db.commit()
    await db.refresh(existing_permission)
    return SuccessResponse()


# 移除权限
@router.delete(
    "/{permission_id}/remove",
    summary="移除权限",
    description="移除某个权限",
    response_model=SuccessResponse,
)
async def remove_permission(
    permission_id: int,
    current_user: User = Depends(
        require_permission(PERMISSION_PATH, PERMISSION_PUT_API)
    ),
    db: AsyncSession = Depends(database.get_db),
):
    result = await db.execute(select(Permission).where(Permission.id == permission_id))
    existing_permission = result.scalar_one_or_none()
    if not existing_permission:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Permission not found"
        )
    await db.delete(existing_permission)
    await db.commit()
    return SuccessResponse()


# 获取权限列表
@router.get(
    "/list",
    summary="获取权限列表",
    description="获取权限列表",
    response_model=list[PermissionResponse],
)
async def list_permissions(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(database.get_db),
):
    result = await db.execute(select(Permission))
    permissions = result.scalars().all()
    return permissions
