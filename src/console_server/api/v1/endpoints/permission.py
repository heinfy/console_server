from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi import APIRouter, Depends, HTTPException, status

from console_server.db import database
from console_server.model.rbac import User, Permission
from console_server.schema.permission import PermissionResponse, PermissionCreate
from console_server.utils.auth import get_current_user


router = APIRouter(prefix="/permission", tags=["permission"])


@router.post(
    "/create",
    summary="创建权限",
    description="创建新权限",
    response_model=PermissionResponse,
)
async def create_permission(
    permission: PermissionCreate,
    current_user: User = Depends(get_current_user),
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
