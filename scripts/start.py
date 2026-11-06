"""加载部署环境配置，然后用同一进程启动 Uvicorn。"""

import argparse
import importlib.util
import os
from pathlib import Path
import shlex
import shutil
import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def boolean(name: str) -> bool:
    value = os.environ[name].strip().lower()
    if value in {"1", "true", "t", "yes", "y", "on"}:
        return True
    if value in {"0", "false", "f", "no", "n", "off"}:
        return False
    raise ValueError(f"{name} 必须是布尔值，例如 true 或 false。")


def integer(name: str, maximum: int | None = None) -> int:
    try:
        value = int(os.environ[name])
    except ValueError:
        raise ValueError(f"{name} 必须是整数。") from None
    if value < 1 or (maximum is not None and value > maximum):
        limit = f"1 到 {maximum}" if maximum is not None else "大于等于 1"
        raise ValueError(f"{name} 必须为 {limit}。")
    return value


def main() -> None:
    parser = argparse.ArgumentParser(
        description="启动 Console Server：默认 prod，支持 dev / pre / test 及自定义环境。",
    )
    parser.add_argument("environment", nargs="?", help="环境名；优先于 ENV 环境变量")
    parser.add_argument("--env-file", help="指定配置文件，优先于 ENV_FILE；相对路径基于项目根目录")
    parser.add_argument("--dry-run", action="store_true", help="检查配置并打印启动命令，不启动服务")
    parser.add_argument(
        "--docker", nargs=argparse.REMAINDER,
        help="使用同一配置运行 Docker Compose；后续参数透传，默认 up -d。其他启动选项写在此前。",
    )
    args = parser.parse_args()

    try:
        from console_server.core.environment import PROJECT_ROOT, load_environment
    except ImportError:
        parser.error("缺少运行依赖，请先执行 uv sync 或安装项目依赖。")
    try:
        env_file = load_environment(args.environment, args.env_file, force=True)
    except ValueError as error:
        parser.error(str(error))
    environment = os.environ["ENV"]
    development = environment == "dev"

    os.chdir(PROJECT_ROOT)
    try:
        debug = boolean("DEBUG")
        reload = boolean("RELOAD")
        port = integer("PORT", 65535)
        workers = integer("WORKERS")
        if reload and not development:
            raise ValueError("热更新仅支持 dev 环境，请设置 RELOAD=false。")
        if reload and workers != 1:
            raise ValueError("热更新不能与多进程同时使用，请设置 WORKERS=1 或 RELOAD=false。")
        if not development:
            missing = [name for name in ("DATABASE_URL", "SECRET_KEY") if not os.environ.get(name, "").strip()]
            if missing:
                raise ValueError(f"{environment} 环境必须显式配置：{', '.join(missing)}。")
        log_level = os.environ["LOG_LEVEL"].strip().lower()
        if log_level not in {"critical", "error", "warning", "info", "debug", "trace"}:
            raise ValueError("LOG_LEVEL 必须是 critical / error / warning / info / debug / trace。")
        host = os.environ["HOST"].strip()
        if not host:
            raise ValueError("HOST 不能为空。")
    except ValueError as error:
        parser.error(str(error))

    os.environ["DEBUG"] = str(debug).lower()
    # Uvicorn 的隐式配置不能绕过这里的热更新规则或额外加载另一份环境文件。
    os.environ.pop("UVICORN_RELOAD", None)
    os.environ.pop("UVICORN_ENV_FILE", None)
    if args.docker is not None:
        if shutil.which("docker") is None:
            parser.error("未找到 Docker，请先安装 Docker 和 Docker Compose。")
        compose_args = args.docker
        if compose_args[:1] == ["--"]:
            compose_args = compose_args[1:]
        command = [
            "docker", "compose", "--project-directory", str(PROJECT_ROOT),
            "--env-file", str(env_file) if env_file is not None else os.devnull,
            "-p", f"console-{environment}", "-f", str(PROJECT_ROOT / "docker-compose.deploy.yaml"),
            *(compose_args or ["up", "-d"]),
        ]
        print(f"环境={environment} 配置文件={env_file or '仅使用系统环境变量'} 容器端口={port}", flush=True)
        if args.dry_run:
            print(shlex.join(command))
            return
        os.execvpe(command[0], command, os.environ)

    if importlib.util.find_spec("uvicorn") is None:
        parser.error("缺少 uvicorn，请先执行 uv sync 或安装项目依赖。")

    command = [
        sys.executable, "-m", "uvicorn", "console_server.main:app",
        "--app-dir", str(PROJECT_ROOT / "src"),
        "--host", host, "--port", str(port),
        "--forwarded-allow-ips", os.environ["FORWARDED_ALLOW_IPS"],
        "--log-level", log_level,
    ]
    if reload:
        command.extend(["--reload", "--reload-dir", str(PROJECT_ROOT / "src")])
    else:
        command.extend(["--workers", str(workers)])

    print(f"环境={environment} 配置文件={env_file or '仅使用系统环境变量'} DEBUG={debug} 热更新={reload} 进程数={workers}", flush=True)
    if args.dry_run:
        print(shlex.join(command))
        return
    # 替换进程，让容器 / systemd 的停止信号直接到达 Uvicorn。
    os.execv(sys.executable, command)


if __name__ == "__main__":
    main()
