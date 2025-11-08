from sqlalchemy import Column, Integer, String, DateTime
from sqlalchemy.orm import DeclarativeBase
from datetime import datetime, timezone


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), index=True)
    email = Column(String(100), unique=True, index=True)
    password = Column(String(255))  # 存储哈希后的密码
    role = Column(String(50), index=True, default="user")


class TokenBlacklist(Base):
    __tablename__ = "token_blacklist"

    id = Column(Integer, primary_key=True, index=True)
    token_hash = Column(
        String(255), unique=True, index=True, nullable=False
    )  # token 的哈希值
    expires_at = Column(
        DateTime(timezone=True), nullable=False, index=True
    )  # token 过期时间（带时区）
    created_at = Column(
        DateTime(timezone=True), default=datetime.now(timezone.utc), nullable=False
    )  # 加入黑名单的时间（带时区）
