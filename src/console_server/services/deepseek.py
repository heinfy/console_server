"""异步连接 DeepSeek，并将上游 SSE 转换成稳定的前端事件协议。"""

import asyncio
import json
from collections.abc import AsyncIterator

import httpx
from anyio import CancelScope
from fastapi import HTTPException

from console_server.core.config import settings
from console_server.schema.chat import ChatRequest


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


class DeepSeekStream:
    def __init__(self, client: httpx.AsyncClient, response: httpx.Response):
        self.client = client
        self.response = response

    async def aclose(self) -> None:
        with CancelScope(shield=True):
            try:
                await self.response.aclose()
            finally:
                await self.client.aclose()

    async def events(self) -> AsyncIterator[str]:
        data_lines: list[str] = []
        usage = None
        finish_reason = None
        lines = self.response.aiter_lines().__aiter__()
        pending = None
        try:
            while True:
                # 保留同一次读取；心跳超时不能取消上游 HTTP 读取。
                if pending is None:
                    pending = asyncio.create_task(anext(lines))
                ready, _ = await asyncio.wait({pending}, timeout=15)
                if not ready:
                    yield ": ping\n\n"
                    continue
                try:
                    line = pending.result()
                except StopAsyncIteration:
                    raise ValueError("DeepSeek stream ended before DONE") from None
                finally:
                    pending = None
                if line.startswith("data:"):
                    data_lines.append(line[5:].lstrip(" "))
                    continue
                if line or not data_lines:
                    continue
                payload = "\n".join(data_lines)
                data_lines.clear()
                if payload.strip() == "[DONE]":
                    yield sse("done", {"finish_reason": finish_reason, "usage": usage})
                    return
                chunk = json.loads(payload)
                if not isinstance(chunk, dict) or "error" in chunk:
                    raise ValueError("Invalid DeepSeek stream event")
                usage = chunk.get("usage") or usage
                for choice in chunk.get("choices", []):
                    finish_reason = choice.get("finish_reason") or finish_reason
                    content = (choice.get("delta") or {}).get("content")
                    if content:
                        if not isinstance(content, str):
                            raise ValueError("Invalid DeepSeek content")
                        yield sse("delta", {"content": content})
        finally:
            with CancelScope(shield=True):
                if pending is not None:
                    pending.cancel()
                    await asyncio.gather(pending, return_exceptions=True)
                await lines.aclose()


async def open_chat_stream(body: ChatRequest) -> DeepSeekStream:
    key = settings.DEEPSEEK_API_KEY.get_secret_value().strip()
    if not key:
        raise HTTPException(503, "AI 服务尚未配置，请联系管理员")
    client = httpx.AsyncClient(
        timeout=httpx.Timeout(settings.DEEPSEEK_READ_TIMEOUT_SECONDS, connect=10),
    )
    response = None
    try:
        request = client.build_request(
            "POST",
            f"{settings.DEEPSEEK_BASE_URL.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Accept": "text/event-stream"},
            json={
                "model": settings.DEEPSEEK_MODEL,
                "messages": [message.model_dump() for message in body.messages],
                "stream": True,
                "stream_options": {"include_usage": True},
                "thinking": {"type": "disabled"},
                "max_tokens": settings.DEEPSEEK_MAX_TOKENS,
            },
        )
        response = await client.send(request, stream=True)
        # 不透传上游错误正文，以免泄露凭证、内部地址或提供商调试信息。
        if response.status_code == 429:
            raise HTTPException(429, "AI 服务繁忙，请稍后再试")
        if response.status_code in (401, 402, 403):
            raise HTTPException(503, "AI 服务凭证或额度不可用，请联系管理员")
        if not response.is_success:
            raise HTTPException(502, "AI 服务暂时不可用，请稍后再试")
        if (
            not response.headers.get("content-type", "")
            .lower()
            .startswith("text/event-stream")
        ):
            raise HTTPException(502, "AI 服务返回了无效的流式响应")
        return DeepSeekStream(client, response)
    except BaseException as exc:
        with CancelScope(shield=True):
            try:
                if response is not None:
                    await response.aclose()
            finally:
                await client.aclose()
        if isinstance(exc, httpx.TimeoutException):
            raise HTTPException(504, "AI 服务连接超时，请稍后再试") from None
        if isinstance(exc, httpx.HTTPError):
            raise HTTPException(502, "无法连接 AI 服务，请稍后再试") from None
        raise
