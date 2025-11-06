from datetime import timedelta
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi import APIRouter, Depends, HTTPException, status

from console_server.db import database
from console_server import models
from console_server import schemas
from console_server import auth
from console_server.core.config import settings


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
    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = auth.create_access_token(
        data={"sub": user.email}, expires_delta=access_token_expires
    )

    return {"access_token": access_token, "token_type": settings.TOKEN_TYPE}


@auth_router.post(
    "/logout",
    summary="退出登录",
    description="退出登录，将当前 JWT token 加入黑名单并撤销",
    status_code=status.HTTP_200_OK,
)
async def logout(
    token: str = Depends(auth.oauth2_scheme),
    current_user: models.User = Depends(auth.get_current_user),
    db: AsyncSession = Depends(database.get_db),
):
    """
    退出登录接口

    - 验证当前用户的 JWT token 有效性
    - 将 token 添加到黑名单以撤销其有效性
    - 客户端也应该删除本地存储的 token

    注意：如果 token 已经在黑名单中，此接口会返回 401 错误
    """
    try:
        # 将 token 添加到黑名单（如果已存在则不会重复添加）
        await auth.add_token_to_blacklist(token, db)

        return {
            "message": "退出登录成功",
            "detail": "Token 已被撤销，请客户端删除本地存储的 token",
        }
    except HTTPException:
        # 如果 token 验证失败，get_current_user 会抛出异常
        # 这里不需要额外处理，异常会被自动传播
        raise
    except Exception as e:
        # 处理其他意外错误
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"退出登录时发生错误: {str(e)}",
        )


@auth_router.post(
    "/cleanup-expired-tokens",
    summary="清理过期 token（管理接口）",
    description="清理黑名单中已过期的 token 记录，释放数据库空间",
    status_code=status.HTTP_200_OK,
)
async def cleanup_expired_tokens(
    current_user: models.User = Depends(auth.get_current_user),
    db: AsyncSession = Depends(database.get_db),
):
    """
    清理过期 token 记录

    这是一个管理接口，用于清理黑名单中已过期的 token。
    建议定期调用此接口（如通过定时任务）以保持数据库整洁。
    """
    try:
        deleted_count = await auth.cleanup_expired_tokens(db)
        return {
            "message": "清理完成",
            "deleted_count": deleted_count,
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"清理过期 token 时发生错误: {str(e)}",
        )
