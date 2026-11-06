from datetime import datetime, timedelta, timezone
from typing import List, Optional
from uuid import UUID, uuid4
import hashlib
from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import and_, exists, select, delete, func, update
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.attributes import set_committed_value

from console_server.db import database

from console_server.model.rbac import User, Role
from console_server.model.token import TokenBlacklist
from console_server.model.auth_session import AuthSession
from console_server.core.config import settings
from console_server.core.permissions import has_permission, can_grant


# 密码加密上下文
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# OAuth2 方案
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

# OAuth2 刷新方案
oauth2_refresh_scheme = OAuth2PasswordBearer(tokenUrl="refresh")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """验证密码"""
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    """生成密码哈希"""
    return pwd_context.hash(password)


def _create_token(data: dict, token_use: str, expires_delta: timedelta, *, jti: UUID | None = None,
                  expires_at: datetime | None = None) -> str:
    """身份必须是规范 UUID，令牌用途由签发入口决定。"""
    to_encode = data.copy()
    subject = to_encode.get("sub")
    if not isinstance(subject, str) or str(UUID(subject)) != subject:
        raise ValueError("Token subject must be a canonical user UUID")
    session_id = to_encode.get("sid")
    if not isinstance(session_id, str) or str(UUID(session_id)) != session_id:
        raise ValueError("Token session must be a canonical UUID")
    now = datetime.now(timezone.utc)
    expire = expires_at if expires_at is not None else now + expires_delta
    to_encode.update({"exp": expire, "iat": now, "jti": str(jti or uuid4()), "token_use": token_use})
    encoded_jwt = jwt.encode(
        to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM
    )
    return encoded_jwt


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    return _create_token(
        data, "access",
        expires_delta if expires_delta is not None else timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
    )


def create_refresh_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    return _create_token(
        data, "refresh",
        expires_delta if expires_delta is not None else timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAY),
    )


def decode_token(token: str, expected_use: str, *, verify_exp: bool = True) -> dict:
    """所有鉴权入口共同验证签名、过期时间、UUID 身份及令牌用途。"""
    try:
        payload = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM],
            # python-jose 会用 require_exp=True 强制打开 verify_exp；
            # 仅退出路径关闭它，随后仍显式检查 exp 存在且类型正确。
            options={"require_exp": verify_exp, "require_sub": True, "require_jti": True,
                     "verify_exp": verify_exp},
        )
    except (TypeError, ValueError, OverflowError) as exc:
        raise JWTError("Invalid token claims") from exc
    if payload.get("token_use") != expected_use:
        raise JWTError("Invalid token use")
    try:
        for claim in ("sub", "sid", "jti"):
            value = payload[claim]
            if not isinstance(value, str) or str(UUID(value)) != value:
                raise ValueError("Invalid UUID")
        if UUID(payload["jti"]).version != 4:
            raise ValueError("jti must be a random UUID")
        if isinstance(payload["exp"], bool) or not isinstance(payload["exp"], int):
            raise ValueError("Invalid expiry")
    except (KeyError, ValueError, TypeError, AttributeError) as exc:
        raise JWTError("Invalid token claims") from exc
    return payload


def verified_token_payload(token: str, expected_use: str, request: Request | None = None) -> dict:
    """同一请求只验签一次；缓存必须同时匹配原始令牌和用途。"""
    cached = getattr(request.state, "verified_token", None) if request is not None else None
    if cached is not None and cached[:2] == (token, expected_use):
        return cached[2]
    payload = decode_token(token, expected_use)
    if request is not None:
        request.state.verified_token = (token, expected_use, payload)
    return payload


def token_blacklist_exists(token: str, now: datetime):
    return exists(select(TokenBlacklist.id).where(
        TokenBlacklist.token_hash == get_token_hash(token), TokenBlacklist.expires_at > now,
    ))


def get_token_hash(token: str) -> str:
    """生成 token 的 SHA256 哈希值"""
    return hashlib.sha256(token.encode()).hexdigest()


async def add_token_to_blacklist(
    token: str, db: AsyncSession, expires_at: Optional[datetime] = None
) -> None:
    """
    将 token 添加到黑名单

    Args:
        token: JWT token 字符串
        db: 数据库会话
        expires_at: token 过期时间，如果为 None 则从 token 中解析
    """
    try:
        # 如果未提供过期时间，从 token 中解析
        if expires_at is None:
            payload = jwt.decode(
                token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM]
            )
            exp = payload.get("exp")
            if exp:
                expires_at = datetime.fromtimestamp(exp, tz=timezone.utc)
            else:
                # 如果没有过期时间，使用默认的过期时间
                expires_at = datetime.now(timezone.utc) + timedelta(
                    minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
                )
    except JWTError:
        # 如果 token 无效，使用默认过期时间
        expires_at = datetime.now(timezone.utc) + timedelta(
            minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
        )

    token_hash = get_token_hash(token)

    # 检查是否已存在于黑名单中
    result = await db.execute(
        select(TokenBlacklist).where(TokenBlacklist.token_hash == token_hash)
    )
    existing = result.scalar_one_or_none()

    if existing is None:
        # 添加到黑名单
        blacklist_entry = TokenBlacklist(
            token_hash=token_hash,
            expires_at=expires_at,
            created_at=datetime.now(timezone.utc),
        )
        db.add(blacklist_entry)
        await db.commit()


async def is_token_blacklisted(token: str, db: AsyncSession) -> bool:
    """
    检查 token 是否在黑名单中

    Args:
        token: JWT token 字符串
        db: 数据库会话

    Returns:
        True 如果 token 在黑名单中，False 否则
    """
    token_hash = get_token_hash(token)
    result = await db.execute(
        select(TokenBlacklist).where(
            TokenBlacklist.token_hash == token_hash,
            TokenBlacklist.expires_at > datetime.now(timezone.utc),  # 只检查未过期的
        )
    )
    return result.scalar_one_or_none() is not None


async def cleanup_expired_tokens(db: AsyncSession) -> int:
    """
    清理过期的黑名单 token

    Args:
        db: 数据库会话

    Returns:
        删除的 token 数量
    """
    # 先查询将要删除的数量
    count_query = select(func.count(TokenBlacklist.id)).where(
        TokenBlacklist.expires_at < datetime.now(timezone.utc)
    )
    count_result = await db.execute(count_query)
    deleted_count = count_result.scalar() or 0

    # 执行删除操作
    result = await db.execute(
        delete(TokenBlacklist).where(
            TokenBlacklist.expires_at < datetime.now(timezone.utc)
        )
    )
    await db.execute(delete(AuthSession).where(AuthSession.expires_at < datetime.now(timezone.utc)))
    await db.commit()
    return deleted_count


async def get_current_user(
    token: str = Depends(oauth2_scheme), db: AsyncSession = Depends(database.get_db),
    request: Request = None,
):
    return await _get_user_from_token(token, db, expected_use="access", request=request)


async def get_user_from_refresh_token(token: str, db: AsyncSession):
    return await _get_user_from_token(token, db, expected_use="refresh")


async def _get_user_from_token(token: str, db: AsyncSession, *, expected_use: str,
                               request: Request | None = None):
    """
    从 token 中获取当前用户，并预加载角色和权限信息

    Returns:
        User: 包含角色和权限信息的用户对象
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="无法验证凭证，请登录",
        headers={"WWW-Authenticate": settings.TOKEN_TYPE},
    )

    try:
        payload = verified_token_payload(token, expected_use, request)
        user_uuid = UUID(payload["sub"])
    except JWTError:
        raise credentials_exception

    # 用户、会话和黑名单合并查询；角色和权限仍按请求读取，不缓存授权状态。
    now = datetime.now(timezone.utc)
    result = await db.execute(
        select(User, AuthSession, token_blacklist_exists(token, now))
        .outerjoin(AuthSession, and_(
            AuthSession.id == UUID(payload["sid"]), AuthSession.user_uuid == User.uuid,
        ))
        .options(selectinload(User.roles).selectinload(Role.permissions))
        .where(User.uuid == user_uuid)
        .execution_options(populate_existing=True)
    )
    row = result.one_or_none()
    if row is None:
        raise credentials_exception
    user, session, blacklisted = row
    if blacklisted:
        raise credentials_exception

    # 读取数据库中的实时状态，使停用立即阻止已有访问令牌及刷新令牌。
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="用户已被停用")

    if (session is None or session.revoked_at is not None or as_utc(session.expires_at) <= now
            or (expected_use == "refresh" and str(session.refresh_jti) != payload["jti"])):
        raise credentials_exception

    # 返回查询到的用户对象（已包含角色和权限信息）
    return user


def as_utc(value: datetime) -> datetime:
    # SQLite 测试返回无时区时间；PostgreSQL timestamptz 始终有时区。
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def issue_session_tokens(user: User, session: AuthSession, *, update_session: bool = True) -> dict:
    deadline = as_utc(session.expires_at)
    now = datetime.now(timezone.utc)
    remaining = deadline - now
    data = {"sub": str(user.uuid), "sid": str(session.id)}
    access = _create_token(data, "access", remaining,
                           expires_at=min(deadline, now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)))
    next_jti = uuid4()
    refresh = _create_token(data, "refresh", remaining, jti=next_jti, expires_at=deadline)
    if update_session:
        session.refresh_jti = next_jti
    return {"access_token": access, "refresh_token": refresh, "token_type": settings.TOKEN_TYPE,
            "refresh_jti": next_jti}


async def start_session(user: User, db: AsyncSession, *, persistent: bool = False) -> tuple[AuthSession, dict]:
    session = AuthSession(id=uuid4(), user_uuid=user.uuid, persistent=persistent,
                          expires_at=datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAY))
    tokens = issue_session_tokens(user, session)
    db.add(session)
    return session, tokens


async def rotate_session(refresh_token: str, db: AsyncSession) -> tuple[AuthSession, dict]:
    try:
        payload = decode_token(refresh_token, "refresh")
    except JWTError as exc:
        raise HTTPException(status_code=401, detail="刷新凭证无效，请重新登录") from exc
    # 所有签发路径先锁用户、再锁会话，与改密/停用触发器使用相同顺序。
    result = await db.execute(select(User).where(User.uuid == UUID(payload["sub"]))
                              .with_for_update().execution_options(populate_existing=True))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="用户已不存在或被停用")
    now = datetime.now(timezone.utc)
    result = await db.execute(select(AuthSession, token_blacklist_exists(refresh_token, now)).where(
        AuthSession.id == UUID(payload["sid"]), AuthSession.user_uuid == user.uuid,
    ).with_for_update().execution_options(populate_existing=True))
    row = result.one_or_none()
    if row is None:
        raise HTTPException(status_code=401, detail="会话已失效，请重新登录")
    session, blacklisted = row
    if session.revoked_at is not None or as_utc(session.expires_at) <= now:
        raise HTTPException(status_code=401, detail="会话已失效，请重新登录")
    if blacklisted or str(session.refresh_jti) != payload["jti"]:
        # 旧刷新令牌重放：整次登录撤销，包括已轮换的访问与刷新令牌。
        session.revoked_at = now
        await db.commit()
        raise HTTPException(status_code=401, detail="检测到刷新令牌重复使用，会话已撤销")
    tokens = issue_session_tokens(user, session, update_session=False)
    # 条件更新只允许当前 jti 被消费一次；签发结果仅在提交成功后返回给客户端。
    changed = await db.execute(update(AuthSession).where(
        AuthSession.id == session.id, AuthSession.user_uuid == user.uuid,
        AuthSession.refresh_jti == UUID(payload["jti"]), AuthSession.revoked_at.is_(None),
        AuthSession.expires_at > datetime.now(timezone.utc),
    ).values(refresh_jti=tokens["refresh_jti"]).execution_options(synchronize_session=False))
    if changed.rowcount != 1:
        await db.rollback()
        raise HTTPException(status_code=401, detail="会话已改变，请重新登录")
    await db.commit()
    set_committed_value(session, "refresh_jti", tokens["refresh_jti"])
    return session, tokens


async def revoke_presented_sessions(request, db: AsyncSession) -> None:
    candidates = [(request.cookies.get("refresh_token"), "refresh")]
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() == "bearer":
        candidates.append((token, "access"))
    for token, token_use in candidates:
        if not token:
            continue
        try:
            # 过期凭证仍可撤销其签名所属会话，不能用于访问业务或刷新。
            payload = decode_token(token, token_use, verify_exp=False)
        except JWTError:
            continue
        await db.execute(update(AuthSession).where(
            AuthSession.id == UUID(payload["sid"]), AuthSession.user_uuid == UUID(payload["sub"]),
            AuthSession.revoked_at.is_(None),
        ).values(revoked_at=datetime.now(timezone.utc)))
    await db.commit()


ADMIN_API = "api:*"


def require_permission(curr_api_path: str, required_permission: str):
    """保留原依赖签名，实际授权统一使用有效权限而非角色名称。"""
    if not required_permission.startswith(f"api:{curr_api_path}:"):
        raise ValueError("权限编码与接口模块不一致")
    return require_any_permission(required_permission)


def permission_names(user: User) -> list[str]:
    return [permission.name for permission in user.permissions] if user.is_active else []


def require_any_permission(*required_permissions: str):
    async def permission_checker(current_user: User = Depends(get_current_user)) -> User:
        if current_user.is_active and any(
            has_permission(permission_names(current_user), required)
            for required in required_permissions
        ):
            return current_user
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail=f"需要权限：{' 或 '.join(required_permissions)}")
    return permission_checker


def ensure_grantable(user: User, permissions) -> None:
    names = permission_names(user)
    if any(not can_grant(names, permission.name) for permission in permissions):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="不能授予或修改超出自身范围的权限")
