from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi import APIRouter, Depends, HTTPException, status

from console_server.db import database
from console_server import models
from console_server import schemas
from console_server import auth


router = APIRouter(prefix="/role", tags=["role"])


@router.post(
    "/create",
    summary="创建角色",
    description="创建新角色",
    response_model=schemas.RoleResponse,
)
async def create_role(
    role: schemas.RoleCreate,
    current_user: models.User = Depends(auth.get_current_user),
    db: AsyncSession = Depends(database.get_db),
):
    # 检查角色名称是否已存在
    result = await db.execute(select(models.Role).where(models.Role.name == role.name))
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Role name already exists"
        )
    # 创建新角色
    new_role = models.Role(
        name=role.name,
        display_name=role.display_name,
        description=role.description,
        is_active=role.is_active,
    )
    db.add(new_role)
    await db.commit()
    await db.refresh(new_role)
    return new_role
