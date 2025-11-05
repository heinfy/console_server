from datetime import timedelta
from sqlalchemy import select
from datetime import timedelta
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi import APIRouter, Depends, HTTPException, status

from console_server.db import database
from console_server import models
from console_server import schemas
from console_server import auth


auth_router = APIRouter(tags=["auth"])


# ✅ 路由
@auth_router.post(
    "/register",
    summary="注册用户",
    description="注册用户",
    response_model=schemas.UserResponse,
)
async def create_user(
    user: schemas.UserCreate, db: AsyncSession = Depends(database.get_db)
):
    # 检查邮箱是否已注册
    result = await db.execute(
        select(models.User).where(models.User.email == user.email)
    )
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Email already registered")

    # 创建新用户，密码哈希处理
    hashed_password = auth.get_password_hash(user.password)
    new_user = models.User(name=user.name, email=user.email, password=hashed_password)
    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)
    return new_user


@auth_router.post(
    "/login",
    summary="登录获取 token",
    description="使用邮箱和密码登录，获取 JWT token",
    response_model=schemas.Token,
)
async def login(
    form_data: schemas.UserLogin, db: AsyncSession = Depends(database.get_db)
):
    # 验证用户
    result = await db.execute(
        select(models.User).where(models.User.email == form_data.email)
    )
    user = result.scalar_one_or_none()

    if not user or not auth.verify_password(form_data.password, str(user.password)):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="邮箱或密码错误",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 创建访问 token
    access_token_expires = timedelta(minutes=auth.ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = auth.create_access_token(
        data={"sub": user.email}, expires_delta=access_token_expires
    )

    return {"access_token": access_token, "token_type": "bearer"}
