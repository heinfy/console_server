# ✅ 先导入标准库和第三方库
import os
from dotenv import load_dotenv
from colorama import init
from console_server.utils.console import print_success, print_info
from console_server.core.config import settings

# 如果 RUNNING_IN_DOCKER 或 NO_COLOR 被设置，禁用颜色
# docker run -e NO_COLOR=1 <IMAGE>
if os.getenv("NO_COLOR") or os.getenv("RUNNING_IN_DOCKER"):
    init(strip=True, convert=False)  # 强制剥离 ANSI 码
else:
    # 初始化 colorama（autoreset=True 表示每次 print 后自动重置样式）
    init(autoreset=True)


env = os.getenv("ENV") or settings.ENV
_debug: bool = False

if env == settings.ENV:
    _debug = settings.DEBUG
else:
    load_dotenv()
    _debug = bool(os.getenv("DEBUG"))

print_info(f"版本: {settings.APP_VERSION}")
print_info(f"环境: {env}")
print_info(f"DEBUG: {_debug}")

from fastapi import FastAPI
from contextlib import asynccontextmanager


# ✅ 第二步：导入本地模块（必须放在前面）
from .db import database
from .api.router import router


@asynccontextmanager
async def lifespan(app: FastAPI):
    print_success("应用启动：初始化数据库")
    await database.init_db()
    yield
    print_info("应用关闭：目前无额外清理任务")


# ✅ 定义 FastAPI 应用
app = FastAPI(
    debug=_debug,
    lifespan=lifespan,
    docs_url="/docs" if _debug else None,
    redoc_url="/redoc" if _debug else None,
    openapi_url="/api/openapi.json" if _debug else None,
)

app.include_router(router, prefix=settings.API_STR)
