from datetime import datetime, timezone
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse

from console_server.core.constants import AUTH_PATH
from console_server.utils.auth import require_any_permission
from console_server.db import database
from console_server.model.rbac import User, Role
from console_server.model.auth_session import AuthSession
from console_server.schema.common import SuccessResponse
from console_server.schema.user import UserResponse, UserCreate, Token, UserLogin
from console_server.utils.auth import (
    get_current_user,
    get_password_hash,
    start_session,
    rotate_session,
    revoke_presented_sessions,
    as_utc,
    verify_password,
    cleanup_expired_tokens,
)
from console_server.core.config import settings
from console_server.core.origin import normalize_origin
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

    role_result = await db.execute(select(Role).where(
        Role.name.in_(("user", "normal")), Role.is_active.is_(True)
    ).order_by(Role.id).with_for_update().execution_options(populate_existing=True))
    roles = {role.name: role for role in role_result.scalars().all()}
    default_role = roles.get("user") or roles.get("normal")
    if not default_role:
        raise HTTPException(status_code=500, detail="请先配置启用的普通用户角色")

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
    request: Request,
    db: AsyncSession = Depends(database.get_db),
):
    validate_cookie_request(request)
    # 验证用户
    result = await db.execute(select(User).where(User.email == form_data.email)
                              .with_for_update().execution_options(populate_existing=True))
    user = result.scalar_one_or_none()

    if not user or not verify_password(form_data.password, str(user.password)):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="邮箱或密码错误",
            headers={"WWW-Authenticate": settings.TOKEN_TYPE},
        )
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户已被停用")

    session, tokens = await start_session(user, db, persistent=form_data.remember_me)
    await db.commit()
    set_refresh_cookie(response, session, tokens["refresh_token"])
    return Token(access_token=tokens["access_token"], token_type=tokens["token_type"])


def set_refresh_cookie(response: Response, session: AuthSession, token: str) -> None:
    max_age = max(0, int((as_utc(session.expires_at) - datetime.now(timezone.utc)).total_seconds()))
    response.set_cookie(
        key="refresh_token", value=token, httponly=True, secure=settings.COOKIE_SECURE,
        samesite="lax", max_age=max_age if session.persistent else None, path="/",
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"


def clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(key="refresh_token", path="/", secure=settings.COOKIE_SECURE,
                           httponly=True, samesite="lax")
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"


def validate_cookie_request(request: Request) -> None:
    # 非简单请求头阻止 HTML 表单伪造；Origin 同时隔离不可信的同站子域。
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        raise HTTPException(status_code=415, detail="会话操作要求 application/json 请求")
    if request.headers.get("x-csrf-protection") != "1":
        raise HTTPException(status_code=403, detail="缺少会话操作保护请求头")
    site = request.headers.get("sec-fetch-site")
    if site == "cross-site":
        raise HTTPException(status_code=403, detail="不接受跨站会话操作")
    origin = request.headers.get("origin")
    referer = request.headers.get("referer")
    if origin is not None or referer is not None:
        try:
            source = normalize_origin(origin if origin is not None else referer, allow_path=origin is None)
            allowed = settings.AUTH_ALLOWED_ORIGINS or [normalize_origin(str(request.base_url), allow_path=True)]
        except ValueError as exc:
            raise HTTPException(status_code=403, detail="无效的会话请求来源") from exc
        if source not in allowed:
            raise HTTPException(status_code=403, detail="不接受该来源的会话操作")
    elif site not in (None, "same-origin"):
        raise HTTPException(status_code=403, detail="无法验证会话请求来源")


@auth_router.post("/logout", summary="退出当前登录会话", response_model=SuccessResponse)
async def logout(request: Request, response: Response,
                 db: AsyncSession = Depends(database.get_db)):
    validate_cookie_request(request)
    # 无需有效 access；过期凭证、已经撤销或无 Cookie 时也能幂等退出。
    await revoke_presented_sessions(request, db)
    clear_refresh_cookie(response)
    return SuccessResponse()


@auth_router.post("/logout-all", summary="退出当前账号的全部登录会话", response_model=SuccessResponse)
async def logout_all(request: Request, response: Response, current_user: User = Depends(get_current_user),
                     db: AsyncSession = Depends(database.get_db)):
    validate_cookie_request(request)
    # 与登录、刷新和改密共用用户行锁，避免全量退出时并发签发遗漏。
    await db.execute(select(User).where(User.uuid == current_user.uuid).with_for_update())
    await db.execute(update(AuthSession).where(
        AuthSession.user_uuid == current_user.uuid, AuthSession.revoked_at.is_(None),
    ).values(revoked_at=datetime.now(timezone.utc)))
    await db.commit()
    clear_refresh_cookie(response)
    return SuccessResponse()


# 清理过期 token
@auth_router.post(
    "/cleanup-expired-tokens",
    summary="清理过期 token",
    description="清理黑名单中已过期的 token 记录，释放数据库空间",
    status_code=status.HTTP_200_OK,
)
async def clean_up_expired_tokens(
    current_user: User = Depends(require_any_permission("api:*")),
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


@auth_router.post("/refresh", response_model=Token)
async def refresh_token(request: Request, response: Response,
                        db: AsyncSession = Depends(database.get_db)):
    validate_cookie_request(request)
    token = request.cookies.get("refresh_token")
    try:
        if not token:
            raise HTTPException(status_code=401, detail="未找到刷新凭证，请重新登录")
        session, tokens = await rotate_session(token, db)
    except HTTPException as exc:
        # 使用实际响应返回失败，保证删除 HttpOnly Cookie 的头不会被异常处理丢弃。
        failure = JSONResponse(status_code=exc.status_code, content={"detail": exc.detail},
                               headers={"WWW-Authenticate": settings.TOKEN_TYPE})
        clear_refresh_cookie(failure)
        return failure
    set_refresh_cookie(response, session, tokens["refresh_token"])
    return Token(access_token=tokens["access_token"], token_type=tokens["token_type"])
