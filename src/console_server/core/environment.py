"""在 Settings 实例化前加载所选环境；系统注入的变量优先。"""

import os
from pathlib import Path
import re

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_ENV = "prod"
DEFAULT_PORT = 8000
ENV_ALIASES = {
    "local": "dev", "development": "dev", "preprod": "pre",
    "staging": "pre", "production": "prod", "testing": "test",
}
STARTUP_DEFAULTS = {
    "HOST": "0.0.0.0",
    "PORT": str(DEFAULT_PORT),
    "WORKERS": "1",
    "LOG_LEVEL": "info",
    "FORWARDED_ALLOW_IPS": "127.0.0.1",
}


def normalize_environment(value: str) -> str:
    name = value.strip().lower()
    name = ENV_ALIASES.get(name, name)
    if not re.fullmatch(r"[a-z][a-z0-9_-]*", name):
        raise ValueError("环境名必须以英文字母开头，且只包含小写字母、数字、下划线或连字符。")
    return name


def load_environment(
    environment: str | None = None,
    env_file: str | None = None,
    *,
    force: bool = False,
) -> Path | None:
    """供启动器、应用和健康检查共用；命令行及系统变量优先于文件。"""
    if not force and os.environ.get("CONSOLE_ENV_LOADED") == "1":
        loaded_file = os.environ.get("CONSOLE_ENV_FILE")
        return Path(loaded_file) if loaded_file else None

    name = normalize_environment(environment or os.environ.get("ENV") or DEFAULT_ENV)
    # 固定选择结果，文件内 ENV 以及 dotenv 插值不能切换环境。
    os.environ["ENV"] = name
    explicit = env_file if env_file is not None else os.environ.get("ENV_FILE")
    if explicit is not None:
        if not explicit.strip():
            raise ValueError("ENV_FILE / --env-file 不能为空。")
        path = Path(explicit).expanduser()
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        if not path.is_file():
            raise ValueError("指定的环境配置文件不存在或不是文件。")
    else:
        path = PROJECT_ROOT / f".env.{name}"
        if name == "dev" and not path.exists():
            path = PROJECT_ROOT / ".env"
        if path.exists() and not path.is_file():
            raise ValueError("环境配置路径不是文件。")
        if not path.exists():
            path = None
    if path is not None:
        path = path.resolve()
        try:
            load_dotenv(path, override=False, encoding="utf-8")
        except (OSError, UnicodeError):
            raise ValueError("环境配置文件无法读取，请检查权限及 UTF-8 编码。") from None

    development = name == "dev"
    defaults = {
        **STARTUP_DEFAULTS,
        "DEBUG": "true" if development else "false",
        "RELOAD": "true" if development else "false",
        "COOKIE_SECURE": "false" if development else "true",
        "CORS_ALLOW_ORIGIN": "http://localhost:5173;http://localhost:8000" if development else "",
    }
    for key, value in defaults.items():
        os.environ.setdefault(key, value)
    os.environ["CONSOLE_ENV_FILE"] = str(path) if path is not None else ""
    os.environ["CONSOLE_ENV_LOADED"] = "1"
    return path

