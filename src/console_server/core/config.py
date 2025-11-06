from __future__ import annotations

from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings

try:  # Python 3.11+
    import tomllib  # type: ignore[attr-defined]
except Exception:  # pragma: no cover
    tomllib = None  # type: ignore[assignment]


def _find_project_root(start: Path) -> Optional[Path]:
    current = start
    for parent in [current, *current.parents]:
        if (parent / "pyproject.toml").exists():
            return parent
    return None


def _read_pyproject_version() -> Optional[str]:
    if tomllib is None:
        return None
    try:
        root = _find_project_root(Path(__file__).resolve())
        if root is None:
            return None
        pyproject_path = root / "pyproject.toml"
        with pyproject_path.open("rb") as f:
            data = tomllib.load(f)
        project = data.get("project") or {}
        version = project.get("version")
        if isinstance(version, str) and version.strip():
            return version.strip()
    except Exception:
        # Silently fall back to None if anything goes wrong
        return None
    return None


_DEFAULT_APP_VERSION = _read_pyproject_version()


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
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1
    TOKEN_TYPE: str = "bearer"  # OAuth2 token 类型，常见值: "bearer", "mac"

    # 定时任务配置
    CLEANUP_EXPIRED_TOKENS_INTERVAL_HOURS: int = 24  # 清理过期 token 的间隔（小时）


settings = Settings()
