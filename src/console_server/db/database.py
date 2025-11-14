from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from console_server.core.config import settings
from console_server.utils.console import print_info

import os

# 支持环境变量配置，便于 Docker 部署
DATABASE_URL = os.getenv("DATABASE_URL", settings.DATABASE_URL)

print_info(f"数据库地址: {DATABASE_URL}")

# 创建异步引擎
engine = create_async_engine(DATABASE_URL, echo=True)

# 创建异步会话工厂
AsyncSessionLocal = async_sessionmaker(
    bind=engine, class_=AsyncSession, expire_on_commit=False
)


# 依赖：获取数据库会话
async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
