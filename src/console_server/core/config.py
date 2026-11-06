from console_server.utils.get_version import read_pyproject_version

from pydantic_settings import BaseSettings
from pydantic import ConfigDict, Field, SecretStr, field_validator, model_validator

from console_server.core.environment import DEFAULT_ENV, load_environment
from console_server.core.origin import normalize_origin


load_environment()


_DEFAULT_APP_VERSION = read_pyproject_version()


class Settings(BaseSettings):
    model_config = ConfigDict(hide_input_in_errors=True)
    # Debug Config
    DEBUG: bool = False

    # App Config
    APP_VERSION: str | None = _DEFAULT_APP_VERSION
    API_STR: str = "/api"
    ENV: str = DEFAULT_ENV
    DATABASE_URL: str = (
        "postgresql+asyncpg://postgres:123456@localhost:5432/console_local_db"
    )

    # DeepSeek 凭证仅用于后端请求，不出现在响应和配置日志中。
    DEEPSEEK_API_KEY: SecretStr = Field(default=SecretStr(""), repr=False)
    DEEPSEEK_BASE_URL: str = "https://api.deepseek.com"
    DEEPSEEK_MODEL: str = Field(default="deepseek-flash", min_length=1)
    DEEPSEEK_MAX_TOKENS: int = Field(default=4096, ge=1, le=32768)
    DEEPSEEK_READ_TIMEOUT_SECONDS: float = Field(default=60, gt=0, le=600)
    CHAT_STREAM_TIMEOUT_SECONDS: float = Field(default=300, gt=0, le=1800)

    # JWT 配置
    SECRET_KEY: str = Field(repr=False)
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60  # 60 * 24 * 7
    REFRESH_TOKEN_EXPIRE_DAY: int = 30
    TOKEN_TYPE: str = "Bearer"
    COOKIE_SECURE: bool = True
    AUTH_ALLOWED_ORIGINS: list[str] = Field(default_factory=list)

    @field_validator("AUTH_ALLOWED_ORIGINS")
    @classmethod
    def validate_auth_origins(cls, values: list[str]) -> list[str]:
        return [normalize_origin(value) for value in values]

    @field_validator("SECRET_KEY")
    @classmethod
    def validate_secret_key(cls, value: str) -> str:
        if len(value.encode("utf-8")) < 32 or value != value.strip():
            raise ValueError("SECRET_KEY 必须是至少 32 字节且不含首尾空白的随机密钥")
        if any(marker in value.lower() for marker in ("change-me", "changeme", "replace", "your-secret")):
            raise ValueError("SECRET_KEY 不能使用示例占位值")
        return value

    @model_validator(mode="after")
    def validate_deployment_database(self):
        if self.ENV != "dev" and ("DATABASE_URL" not in self.model_fields_set or not self.DATABASE_URL.strip()):
            raise ValueError("开发环境以外必须显式配置 DATABASE_URL")
        return self

    # 定时任务配置
    CLEANUP_EXPIRED_TOKENS_INTERVAL_HOURS: int = 24  # 清理过期 token 的间隔（小时）

    # 分页配置
    DEFAULT_PAGE_SIZE: int = 10
    MAX_PAGE_SIZE: int = 100


settings = Settings()
