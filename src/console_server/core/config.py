from console_server.utils.get_version import read_pyproject_version

from pydantic_settings import BaseSettings


_DEFAULT_APP_VERSION = read_pyproject_version()


class Settings(BaseSettings):
    # Debug Config
    DEBUG: bool = True

    # App Config
    APP_VERSION: str | None = _DEFAULT_APP_VERSION
    API_STR: str = "/api"
    ENV: str = "local"
    DATABASE_URL: str = (
        "postgresql+asyncpg://postgres:123456@localhost:5432/console_local_db"
    )

    # JWT 配置
    SECRET_KEY: str = (
        "94UWJn0HcLAqFTotsiJzT9Hyb61WakL+4Ox7HYN6yac6ou0qTpc8uAiCsf+YHJo1BXJ+6el4nuzs+pFfLXQUsQ=="  # 在生产环境中应该使用环境变量
    )
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1  # 60 * 24 * 7
    REFRESH_TOKEN_EXPIRE_DAY: int = 30
    TOKEN_TYPE: str = "Bearer"
    COOKIE_SECURE: bool = True

    # 定时任务配置
    CLEANUP_EXPIRED_TOKENS_INTERVAL_HOURS: int = 24  # 清理过期 token 的间隔（小时）

    # 分页配置
    DEFAULT_PAGE_SIZE: int = 10
    MAX_PAGE_SIZE: int = 100


settings = Settings()
