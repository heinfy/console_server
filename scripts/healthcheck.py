"""供 Docker 探测 HTTP 存活，兼容环境变量和挂载配置文件。"""

import os
from urllib.error import URLError
from urllib.request import urlopen

from console_server.core.environment import load_environment


load_environment()
port = os.environ["PORT"]
prefix = os.environ.get("API_STR", "/api").rstrip("/")
try:
    with urlopen(f"http://127.0.0.1:{port}{prefix}/health", timeout=3) as response:
        raise SystemExit(0 if response.status == 200 else 1)
except (URLError, TimeoutError, ValueError):
    raise SystemExit(1)
