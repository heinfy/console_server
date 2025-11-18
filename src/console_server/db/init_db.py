from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, insert

from console_server.model.rbac import Base, Role, User, user_roles
from console_server.utils.auth import get_password_hash
from .database import engine
import logging

logger = logging.getLogger(__name__)


# 初始化数据库（创建表）—— 仅用于开发
async def init_db():
    """开发环境初始化：建表、插入默认角色和 admin 用户"""
    # 第一步：创建所有表
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # 第二步：插入初始数据（使用 AsyncSession）
    async with AsyncSession(engine) as session:
        await _init_roles(session)
        await _init_admin_user(session)
        await session.commit()
        logger.info("✅ 数据库初始化完成：表、角色、admin 用户已创建")


async def _init_roles(session: AsyncSession):
    """初始化默认角色"""
    default_roles = [
        {
            "name": "user",
            "display_name": "普通用户",
            "description": "普通用户，只有基本的访问权限。",
            "is_active": True,
        },
        {
            "name": "admin",
            "display_name": "管理员",
            "description": "管理员用户，拥有所有的权限。",
            "is_active": True,
        },
    ]
    for role_data in default_roles:
        result = await session.execute(
            select(Role).where(Role.name == role_data["name"])
        )
        if not result.scalar_one_or_none():
            session.add(Role(**role_data))
            logger.info(f"✅  创建角色: {role_data['name']}")


async def _init_admin_user(session: AsyncSession):
    """初始化 admin 用户（仅当不存在时）"""
    admin_email = "admin@example.com"
    result = await session.execute(select(User).where(User.email == admin_email))
    if not result.scalar_one_or_none():
        admin_user = User(
            name="Admin",
            email=admin_email,
            password=get_password_hash("admin123"),  # 默认密码，首次登录后应修改！
            description="管理员用户，具有所有权限。",
        )
        session.add(admin_user)
        await session.flush()  # 获取 admin_user.id

        # 关联 admin 角色
        result = await session.execute(select(Role).where(Role.name == "admin"))
        admin_role = result.scalar_one_or_none()
        if admin_role:
            # 直接向中间表插入关联，避免触发 ORM 在 collection 上的懒加载（async 环境下会报 MissingGreenlet）
            await session.execute(
                insert(user_roles).values(user_id=admin_user.id, role_id=admin_role.id)
            )
            logger.info("✅  创建 admin 用户并分配 admin 角色 (通过中间表插入)")
        else:
            logger.warning("⚠️ 未找到 'admin' 角色，无法分配")
