from datetime import timedelta
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from console_server.core.constants import AUTH_PATH
from console_server.db import database
from console_server.model.rbac import User, Role
from console_server.schema.common import SuccessResponse
from console_server.schema.user import UserResponse, UserCreate, Token, UserLogin
from console_server.utils.auth import (
    get_current_user,
    get_password_hash,
    create_access_token,
    is_token_blacklisted,
    verify_password,
    oauth2_scheme,
    cleanup_expired_tokens,
    add_token_to_blacklist,
)
from console_server.core.config import settings
from console_server.utils.console import print_success


auth_router = APIRouter(
    prefix=f"/{AUTH_PATH}",
    tags=[AUTH_PATH],
)


# ✅ 路由
@auth_router.post(
    "/register",
    summary="注册用户",
    description="注册用户并分配默认角色",
    response_model=UserResponse,
)
async def create_user(user: UserCreate, db: AsyncSession = Depends(database.get_db)):
    # 检查邮箱是否已注册
    result = await db.execute(select(User).where(User.email == user.email))
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Email already registered"
        )

    # 创建新用户，密码哈希处理
    hashed_password = get_password_hash(user.password)
    new_user = User(name=user.name, email=user.email, password=hashed_password)

    # 🔑 关键：查找默认角色 "user"
    role_result = await db.execute(select(Role).where(Role.name == "user"))
    default_role = role_result.scalar_one_or_none()
    if not default_role:
        raise HTTPException(status_code=500, detail="Default 'user' role not found")

    new_user.roles.append(default_role)

    db.add(new_user)
    await db.commit()
    # ⚠️ 重要：显式加载 roles（避免 lazy load 失败）
    await db.refresh(new_user, ["roles"])

    # 直接构建包含角色信息的字典
    user_data = {
        "id": new_user.id,
        "name": new_user.name,
        "email": new_user.email,
        "roles": [role.name for role in new_user.roles],
    }

    return user_data


# 登录
@auth_router.post(
    "/login",
    summary="登录获取 token",
    description="使用邮箱和密码登录，获取 JWT token",
    response_model=Token,
)
async def login(
    form_data: UserLogin,
    response: Response,
    db: AsyncSession = Depends(database.get_db),
):
    # 验证用户
    result = await db.execute(select(User).where(User.email == form_data.email))
    user = result.scalar_one_or_none()

    if not user or not verify_password(form_data.password, str(user.password)):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="邮箱或密码错误",
            headers={"WWW-Authenticate": settings.TOKEN_TYPE},
        )

    # 创建访问 token
    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={
            "sub": user.email,
            "is_active": user.is_active,
        },
        expires_delta=access_token_expires,
    )

    # 创建刷新 token
    refresh_token = create_access_token(
        data={
            "sub": user.email,
        },
        expires_delta=timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAY),
    )
    # 设置 refresh_token 到 Cookie
    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        httponly=True,
        secure=settings.COOKIE_SECURE,  # 在生产环境中使用 HTTPS 时设为 True
        samesite="lax",
        max_age=settings.REFRESH_TOKEN_EXPIRE_DAY * 24 * 60 * 60,  # 转换为秒
        path="/",
    )

    return {
        "access_token": access_token,
        "token_type": settings.TOKEN_TYPE,
    }


# 登出
@auth_router.post(
    "/logout",
    summary="退出登录",
    description="退出登录，将当前 JWT token 和 refresh token 加入黑名单并撤销",
    status_code=status.HTTP_200_OK,
)
async def logout(
    request: Request,
    response: Response,
    access_token: str = Depends(oauth2_scheme),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(database.get_db),
):
    """
    退出登录接口

    - 验证当前用户的 JWT token 有效性
    - 将 access token 和 refresh token 添加到黑名单以撤销其有效性
    - 清除客户端 Cookie 中的 refresh token

    注意：如果 token 已经在黑名单中，此接口会返回 401 错误
    """
    try:
        # 将 access token 添加到黑名单（如果已存在则不会重复添加）
        await add_token_to_blacklist(access_token, db)

        # 从 cookies 中获取 refresh token
        refresh_token = request.cookies.get("refresh_token")
        if refresh_token:
            # 将 refresh token 添加到黑名单
            await add_token_to_blacklist(refresh_token, db)

        # 清除 Cookie 中的 refresh_token
        response.delete_cookie(
            key="refresh_token",
            path="/",
            secure=settings.COOKIE_SECURE,
            httponly=True,
            samesite="lax",
        )

        print_success(f"用户 {current_user.name} 退出登录")

        return SuccessResponse()
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


# 清理过期 token
@auth_router.post(
    "/cleanup-expired-tokens",
    summary="清理过期 token",
    description="清理黑名单中已过期的 token 记录，释放数据库空间",
    status_code=status.HTTP_200_OK,
)
async def clean_up_expired_tokens(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(database.get_db),
):
    """
    清理过期 token 记录

    这是一个管理接口，用于清理黑名单中已过期的 token。
    建议定期调用此接口（如通过定时任务）以保持数据库整洁。
    """
    try:
        deleted_count = await cleanup_expired_tokens(db)
        print(f"已清理 {deleted_count} 个过期 token")
        return SuccessResponse()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"清理过期 token 时发生错误: {str(e)}",
        )


# 如果 access_token 过期，根据 refresh_token 判断是否重新登录，还是刷新 access_token
@auth_router.get(
    "/refresh",
    response_model=Token,
    status_code=status.HTTP_200_OK,
)
async def refresh_token(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(database.get_db),
) -> Token:
    """
    刷新访问令牌

    当 access_token 过期时，使用存储在 cookies 中的 refresh_token 获取新的访问令牌。
    如果 refresh_token 也过期或被撤销，则要求用户重新登录。
    """
    try:
        # 从 cookies 中获取 refresh_token
        refresh_token = request.cookies.get("refresh_token")
        if not refresh_token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="未找到有效的刷新令牌，请重新登录",
                headers={"WWW-Authenticate": "Bearer", "Location": "/login"},
            )
        # 检查 refresh_token 是否在黑名单中
        if await is_token_blacklisted(refresh_token, db):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="刷新令牌已被撤销，请重新登录",
                headers={"WWW-Authenticate": "Bearer", "Location": "/login"},
            )

        # 验证并获取用户信息
        user = await get_current_user(refresh_token, db)

        # 创建新的访问令牌
        access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
        new_access_token = create_access_token(
            data={
                "sub": user.email,
                "is_active": user.is_active,
            },
            expires_delta=access_token_expires,
        )

        # 创建新的刷新令牌
        new_refresh_token = create_access_token(
            data={
                "sub": user.email,
            },
            expires_delta=timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAY),
        )

        # 将新的 refresh_token 设置到 Cookie 中
        response.set_cookie(
            key="refresh_token",
            value=new_refresh_token,
            httponly=True,
            secure=settings.COOKIE_SECURE,
            samesite="lax",
            max_age=settings.REFRESH_TOKEN_EXPIRE_DAY * 24 * 60 * 60,  # 转换为秒
            path="/",
        )

        # 将旧的 refresh_token 加入黑名单
        await add_token_to_blacklist(refresh_token, db)

        return Token(
            access_token=new_access_token,
            token_type=settings.TOKEN_TYPE,
        )

    except HTTPException as he:
        # 如果是认证相关的异常，抛出给前端处理跳转
        raise HTTPException(
            status_code=420,
            detail="认证已过期，请重新登录",
            headers={"WWW-Authenticate": "Bearer", "Location": "/login"},
        )
    except Exception as e:
        # 处理其他异常
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"刷新令牌时发生错误: {str(e)}",
        )
