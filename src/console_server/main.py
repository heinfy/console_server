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

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from contextlib import asynccontextmanager
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
import logging

# ✅ 第二步：导入本地模块（必须放在前面）
from .db import database
from .api.router import router
from . import auth
from .core.config import settings
from .rbac.enforcer import enforce, get_enforcer

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 创建全局调度器
scheduler = AsyncIOScheduler()


async def cleanup_expired_tokens_task():
    """定时清理过期 token 的任务"""
    try:
        async with database.AsyncSessionLocal() as db:
            deleted_count = await auth.cleanup_expired_tokens(db)
            if deleted_count > 0:
                logger.info(f"定时任务：清理了 {deleted_count} 个过期 token")
            else:
                logger.debug("定时任务：没有需要清理的过期 token")
    except Exception as e:
        logger.error(f"定时任务执行失败：{str(e)}", exc_info=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    print_success("应用启动：初始化数据库")
    await database.init_db()

    # 启动定时任务
    print_success("应用启动：启动定时任务")
    scheduler.add_job(
        cleanup_expired_tokens_task,
        trigger=IntervalTrigger(hours=settings.CLEANUP_EXPIRED_TOKENS_INTERVAL_HOURS),
        id="cleanup_expired_tokens",
        name="清理过期 token",
        replace_existing=True,
    )
    scheduler.start()
    print_info(
        f"定时任务已启动：每 {settings.CLEANUP_EXPIRED_TOKENS_INTERVAL_HOURS} 小时执行一次清理"
    )

    yield

    # 关闭调度器
    print_info("应用关闭：停止定时任务")
    scheduler.shutdown(wait=False)
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


class RBACMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # 允许的公开路径
        public_paths = {
            f"{settings.API_STR}/health",
            f"{settings.API_STR}/login",
            f"{settings.API_STR}/register",
        }
        path = request.url.path
        method = request.method.upper()

        # 预检/公开/文档放行
        if (
            method == "OPTIONS"
            or path in public_paths
            or path.startswith("/docs")
            or path.startswith("/redoc")
            or path.startswith("/api/openapi.json")
        ):
            return await call_next(request)

        # 尝试解析当前用户
        authorization: str | None = request.headers.get("Authorization")
        if not authorization or not authorization.startswith("Bearer "):
            return JSONResponse(status_code=401, content={"detail": "未认证"})
        token = authorization.split(" ", 1)[1]

        try:
            async with database.AsyncSessionLocal() as db:
                user = await auth.get_current_user(token=token, db=db)
        except Exception:
            return JSONResponse(status_code=401, content={"detail": "认证失败"})

        subject = user.role or "user"

        # API 访问鉴权
        if not enforce(subject, path, method):
            return JSONResponse(status_code=403, content={"detail": "无权限"})

        return await call_next(request)


# 初始化 Enforcer（懒加载亦可，这里确保启动时加载校验配置可用）
get_enforcer()
app.add_middleware(RBACMiddleware)
