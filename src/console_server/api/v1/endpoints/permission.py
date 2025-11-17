from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi import APIRouter, Depends, HTTPException, status

from console_server.db import database
from console_server import models
from console_server import schemas
from console_server import auth


router = APIRouter(prefix="/permission", tags=["permission"])


@router.post(
    "/create",
    summary="创建权限",
    description="创建新权限",
    response_model=schemas.PermissionResponse,
)
async def create_permission(
    permission: schemas.PermissionCreate,
    current_user: models.User = Depends(auth.get_current_user),
    db: AsyncSession = Depends(database.get_db),
):
    # 检查权限名称是否已存在
    result = await db.execute(
        select(models.Permission).where(models.Permission.name == permission.name)
    )
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Permission name already exists",
        )
    # 创建新权限
    new_permission = models.Permission(
        name=permission.name,
        display_name=permission.display_name,
        description=permission.description,
    )
    db.add(new_permission)
    await db.commit()
    await db.refresh(new_permission)
    return new_permission
