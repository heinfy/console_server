from datetime import datetime, timedelta, timezone
from typing import Optional
import hashlib
from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from sqlalchemy.orm import selectinload

from console_server.db import database

from console_server import models, schemas
from console_server.core.config import settings


# 密码加密上下文
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# OAuth2 方案
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """验证密码"""
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    """生成密码哈希"""
    return pwd_context.hash(password)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    """创建 JWT token"""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(
        to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM
    )
    return encoded_jwt


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
        select(models.TokenBlacklist).where(
            models.TokenBlacklist.token_hash == token_hash
        )
    )
    existing = result.scalar_one_or_none()

    if existing is None:
        # 添加到黑名单
        blacklist_entry = models.TokenBlacklist(
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
        select(models.TokenBlacklist).where(
            models.TokenBlacklist.token_hash == token_hash,
            models.TokenBlacklist.expires_at
            > datetime.now(timezone.utc),  # 只检查未过期的
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
    result = await db.execute(
        delete(models.TokenBlacklist).where(
            models.TokenBlacklist.expires_at < datetime.now(timezone.utc)
        )
    )
    await db.commit()
    return result.rowcount


async def get_current_user(
    token: str = Depends(oauth2_scheme), db: AsyncSession = Depends(database.get_db)
):
    """从 token 中获取当前用户，并检查 token 是否在黑名单中"""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="无法验证凭证，请登录",
        headers={"WWW-Authenticate": "Bearer"},
    )

    # 检查 token 是否在黑名单中
    if await is_token_blacklisted(token, db):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token 已被撤销，请重新登录",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 解码 JWT token，验证其有效性
    try:
        # 使用密钥和算法解码 token，获取 payload 数据
        payload = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM]
        )
        # 从 payload 中提取用户邮箱（"sub" 字段通常存储用户标识）
        email: str = payload.get("sub")
        # 如果邮箱为空，说明 token 无效，抛出认证异常
        if email is None:
            raise credentials_exception
    except JWTError:
        # 如果 token 解码失败（过期、格式错误等），抛出认证异常
        raise credentials_exception

    # 根据邮箱从数据库中查询用户信息
    # 预加载 roles 关系，避免在序列化时触发懒加载（会在 async 环境中触发 greenlet 错误）
    result = await db.execute(
        select(models.User)
        .options(selectinload(models.User.roles))
        .where(models.User.email == email)
    )
    user = result.scalar_one_or_none()
    # 如果用户不存在，抛出认证异常
    if user is None:
        raise credentials_exception
    # 返回查询到的用户对象
    return user
