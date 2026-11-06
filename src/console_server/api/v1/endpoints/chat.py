import asyncio
from contextlib import asynccontextmanager

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from console_server.core.config import settings
from console_server.db import database
from console_server.schema.chat import ChatRequest
from console_server.services.deepseek import open_chat_stream, sse
from console_server.utils.auth import get_current_user, oauth2_scheme, require_permission


CHAT_USE_PERMISSION = "api:chat:use"
router = APIRouter(prefix="/chat", tags=["AI 对话"])
check_chat_permission = require_permission("chat", CHAT_USE_PERMISSION)


async def require_chat_access(request: Request, token: str = Depends(oauth2_scheme)):
    # 复用实时用户、会话和角色校验，并在生成开始前释放数据库连接。
    async with asynccontextmanager(database.get_db)() as db:
        user = await get_current_user(token=token, db=db, request=request)
        await check_chat_permission(user)


@router.post("/stream", dependencies=[Depends(require_chat_access)])
async def chat_stream(body: ChatRequest):
    # 先连接和检查上游，确保配置、额度等错误能以正常 HTTP 状态返回。
    upstream = await open_chat_stream(body)

    async def generate():
        try:
            async with asyncio.timeout(settings.CHAT_STREAM_TIMEOUT_SECONDS):
                yield ": connected\n\n"
                async for event in upstream.events():
                    yield event
        except (TimeoutError, httpx.TimeoutException):
            yield sse("error", {"message": "生成超时，请重试或缩短问题"})
        except (httpx.HTTPError, ValueError, TypeError, AttributeError):
            yield sse("error", {"message": "生成中断，请重新发送"})
        finally:
            # 浏览器停止、页面卸载或网络断开都会取消迭代并关闭上游。
            await upstream.aclose()

    return StreamingResponse(
        generate(), media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        background=BackgroundTask(upstream.aclose),
    )
