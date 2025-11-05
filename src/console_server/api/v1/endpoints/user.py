from datetime import timedelta
from sqlalchemy import select, insert
from typing import List
from datetime import timedelta
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi import APIRouter, Depends, HTTPException, status

from console_server.db import database
from console_server import models
from console_server import schemas
from console_server import auth


router = APIRouter(prefix="/user", tags=["user"])


@router.get(
    "/current",
    summary="获取当前用户信息",
    description="使用 JWT token 获取当前登录用户的信息",
    response_model=schemas.UserResponse,
)
async def read_users_me(current_user: models.User = Depends(auth.get_current_user)):
    return current_user


@router.get(
    "/users",
    summary="获取用户列表",
    description="获取用户列表",
    response_model=List[schemas.UserResponse],
)
async def read_users(db: AsyncSession = Depends(database.get_db)):
    result = await db.execute(select(models.User))
    users = result.scalars().all()
    return users
