from datetime import datetime, timezone
from sqlalchemy.orm import DeclarativeBase, relationship
from sqlalchemy import (
    Table,
    Column,
    Integer,
    String,
    DateTime,
    Boolean,
    Text,
    ForeignKey,
)
from sqlalchemy.sql import func

from .common import Base


# 中间表（无主键类）
user_roles = Table(
    "user_roles",
    Base.metadata,
    Column("user_id", Integer, ForeignKey("users.id"), primary_key=True),
    Column("role_id", Integer, ForeignKey("roles.id"), primary_key=True),
)

# 角色-权限关联表（无主键类）
role_permissions = Table(
    "role_permissions",
    Base.metadata,
    Column("role_id", Integer, ForeignKey("roles.id"), primary_key=True),
    Column("permission_id", Integer, ForeignKey("permissions.id"), primary_key=True),
)


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False, index=True)  # 必填
    email = Column(String(100), unique=True, index=True, nullable=False)  # 必填
    password = Column(String(255), nullable=False)  # 通常密码也是必填的
    description = Column(
        String(128), nullable=True, index=True
    )  # 不必填，明确设置为 nullable=True
    is_active = Column(
        Boolean, default=True, nullable=False, index=True
    )  # 新增的激活状态字段

    # 创建时间 - 只在创建时设置
    created_at = Column(
        DateTime(timezone=True), default=func.now(), nullable=False  # 使用数据库的函数
    )

    # 更新时间 - 每次更新时自动刷新
    updated_at = Column(
        DateTime(timezone=True),
        default=func.now(),  # 默认值
        onupdate=func.now(),  # 更新时自动设置当前时间
        nullable=False,
    )

    # 多对多关系
    roles = relationship("Role", secondary=user_roles, back_populates="users")

    @property
    def permissions(self):
        perms = set()
        for role in self.roles:
            for permission in role.permissions:
                perms.add(permission)
        return list(perms)

    def __repr__(self):
        return f"<User(id={self.id}, name='{self.name}', email='{self.email}', is_active={self.is_active})>"


class Role(Base):
    __tablename__ = "roles"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(
        String(64), unique=True, nullable=False, index=True
    )  # 角色标识，必填且唯一
    display_name = Column(String(128), nullable=False, index=True)  # 显示名称，必填
    description = Column(Text, nullable=True)  # 描述，使用 Text 类型，不必填
    is_active = Column(Boolean, default=True, nullable=False, index=True)  # 是否启用

    # 创建时间 - 只在创建时设置
    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),  # 使用数据库服务器时间
        nullable=False,
    )

    # 更新时间 - 每次更新时自动刷新
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),  # 默认值
        onupdate=func.now(),  # 更新时自动设置当前时间
        nullable=False,
    )

    users = relationship("User", secondary=user_roles, back_populates="roles")
    permissions = relationship(
        "Permission", secondary=role_permissions, back_populates="roles"
    )

    def __repr__(self):
        return f"<Role(id={self.id}, name='{self.name}', display_name='{self.display_name}', is_active={self.is_active})>"


class Permission(Base):
    __tablename__ = "permissions"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(128), unique=True, nullable=False, index=True)
    display_name = Column(String(256), nullable=False, index=True)
    description = Column(Text, nullable=True)

    is_deletable = Column(Boolean, default=False, nullable=False)
    is_editable = Column(Boolean, default=False, nullable=False)

    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    roles = relationship(
        "Role", secondary=role_permissions, back_populates="permissions"
    )

    def __repr__(self):
        return f"<Permission(id={self.id}, name='{self.name}', display_name='{self.display_name}')>"
