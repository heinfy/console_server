"""真实 JWT、会话、RBAC 和 ASGI；上游通过 MockTransport 隔离。"""

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
import unittest
from unittest.mock import patch

import httpx
from pydantic import SecretStr, ValidationError
from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

import test_auth_tokens as token_tests
from console_server.api.v1.endpoints import chat
from console_server.core.config import Settings, settings
from console_server.core.permissions import effective_capabilities
from console_server.db.database import get_db
from console_server.model.rbac import Permission, Role, User, user_roles
from console_server.schema.chat import ChatRequest
from console_server.services import deepseek


CLIENT_FACTORY = httpx.AsyncClient
PROMPT = {"messages": [{"role": "user", "content": "你好"}]}


class FragmentedBody(httpx.AsyncByteStream):
    def __init__(self, content: str):
        self.content = content.encode()
        self.closed = False

    async def __aiter__(self):
        for byte in self.content:
            await asyncio.sleep(0)
            yield bytes([byte])

    async def aclose(self):
        self.closed = True


def upstream_text(content="你好", finish="stop"):
    chunks = [
        {"choices": [{"delta": {"role": "assistant", "content": ""}}]},
        {"choices": [{"delta": {"content": content}}]},
        {"choices": [{"delta": {}, "finish_reason": finish}], "usage": {"total_tokens": 12}},
    ]
    return ": keep-alive\r\n\r\n" + "".join(
        "data: " + json.dumps(chunk, ensure_ascii=False) + "\r\n\r\n" for chunk in chunks
    ) + "data: [DONE]\r\n\r\n"


class ChatEndpointTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.fixture = token_tests.TokenIdentityTests()
        self.fixture.password_hash = "unused-for-chat"
        self.fixture.setUp()
        self.fixture.app.include_router(chat.router, prefix="/api/v1")
        self.token = self.fixture.tokens()["access_token"]
        with Session(self.fixture.engine) as db:
            db.get(User, 1).roles = [Role(name="ai_user", display_name="AI 对话用户", is_active=True,
                permissions=[Permission(name="api:chat:use", display_name="AI 对话")])]
            db.commit()
        self.db_closed = False
        database = self.fixture.app.dependency_overrides[get_db]

        async def short_database():
            self.db_closed = False
            try:
                async with asynccontextmanager(database)() as db:
                    yield db
            finally:
                self.db_closed = True

        self.db_patch = patch.object(chat.database, "get_db", short_database)
        self.db_patch.start()
        self.key_patch = patch.object(settings, "DEEPSEEK_API_KEY", SecretStr("fake-provider-key"))
        self.key_patch.start()
        self.requests = []
        self.provider_clients = []
        self.status = 200
        self.provider_text = upstream_text()
        self.provider_headers = {"content-type": "text/event-stream"}
        self.provider_error = None

        def respond(request):
            self.assertTrue(self.db_closed, "生成前必须释放数据库会话")
            self.requests.append(request)
            if self.provider_error:
                raise self.provider_error
            self.provider_body = FragmentedBody(self.provider_text)
            return httpx.Response(self.status, headers=self.provider_headers, stream=self.provider_body)

        def provider_client(**kwargs):
            client = CLIENT_FACTORY(transport=httpx.MockTransport(respond), **kwargs)
            self.provider_clients.append(client)
            return client

        self.client = CLIENT_FACTORY(transport=httpx.ASGITransport(app=self.fixture.app), base_url="http://test")
        self.provider_patch = patch.object(deepseek.httpx, "AsyncClient", side_effect=provider_client)
        self.provider_patch.start()

    async def asyncTearDown(self):
        await self.client.aclose()
        self.provider_patch.stop()
        self.key_patch.stop()
        self.db_patch.stop()
        self.fixture.tearDown()

    async def request(self, body=None, token=None):
        return await self.client.post("/api/v1/chat/stream", json=body or PROMPT,
            headers={"Authorization": f"Bearer {token or self.token}"})

    async def test_stream_real_auth_unicode_finish_usage_and_release(self):
        response = await self.request()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.headers["x-accel-buffering"], "no")
        self.assertIn('event: delta\ndata: {"content": "你好"}', response.text)
        self.assertIn('"finish_reason": "stop"', response.text)
        self.assertIn('"total_tokens": 12', response.text)
        self.assertNotIn("fake-provider-key", response.text)
        payload = json.loads(self.requests[0].content)
        self.assertEqual(payload["messages"], PROMPT["messages"])
        self.assertTrue(payload["stream"])
        self.assertEqual(payload["thinking"], {"type": "disabled"})
        self.assertEqual(self.requests[0].headers["authorization"], "Bearer fake-provider-key")
        self.assertTrue(self.provider_body.closed)
        self.assertTrue(self.provider_clients[0].is_closed)

    async def test_unauthenticated_and_refresh_token_never_call_provider(self):
        response = await self.client.post("/api/v1/chat/stream", json=PROMPT)
        self.assertEqual(response.status_code, 401)
        self.assertEqual((await self.request(token=self.fixture.tokens()["refresh_token"])).status_code, 401)
        self.assertFalse(self.requests)

    async def test_role_name_alone_is_not_permission(self):
        with Session(self.fixture.engine) as db:
            db.scalar(select(Role).where(Role.name == "ai_user")).permissions = []
            db.commit()
        self.assertEqual((await self.request()).status_code, 403)
        self.assertFalse(self.requests)

    async def test_role_disable_and_revocation_take_effect_with_same_token(self):
        for mutation in ("disable", "remove"):
            with self.subTest(mutation=mutation), Session(self.fixture.engine) as db:
                role = db.scalar(select(Role).where(Role.name == "ai_user"))
                role.is_active = mutation != "disable"
                if mutation == "remove":
                    db.execute(delete(user_roles).where(user_roles.c.user_id == 1))
                db.commit()
            self.assertEqual((await self.request()).status_code, 403)
        self.assertFalse(self.requests)

    async def test_revoked_session_is_rejected(self):
        with Session(self.fixture.engine) as db:
            session = db.scalar(select(token_tests.AuthSession).where(token_tests.AuthSession.user_uuid == self.fixture.user_uuid))
            session.revoked_at = token_tests.datetime.now(token_tests.timezone.utc)
            db.commit()
        self.assertEqual((await self.request()).status_code, 401)
        self.assertFalse(self.requests)

    async def test_global_admin_retains_chat_capability(self):
        with Session(self.fixture.engine) as db:
            role = db.scalar(select(Role).where(Role.name == "ai_user"))
            role.permissions = [Permission(name="api:*", display_name="全局")]
            db.commit()
        self.assertEqual((await self.request()).status_code, 200)
        self.assertIn("api:chat:use", effective_capabilities(["api:*"]))
        self.assertIn("menu:chat", effective_capabilities(["api:*"]))
        self.assertNotIn("menu:chat", effective_capabilities(["menu:*", "api:user:*"]))

    async def test_invalid_payloads_never_call_provider(self):
        for body in (
            {"messages": []},
            {"messages": [{"role": "system", "content": "injected"}]},
            {"messages": [{"role": "user", "content": " "}]},
            {"messages": [{"role": "user", "content": "a" * 16001}]},
            {"messages": [{"role": "user", "content": "a"}] * 2},
            {**PROMPT, "model": "expensive-client-model"},
        ):
            with self.subTest(body=str(body)[:80]):
                self.assertEqual((await self.request(body)).status_code, 422)
        self.assertFalse(self.requests)

    async def test_multi_turn_history_is_forwarded(self):
        body = {"messages": [
            {"role": "user", "content": "第一问"},
            {"role": "assistant", "content": "第一答"},
            {"role": "user", "content": "第二问"},
        ]}
        self.assertEqual((await self.request(body)).status_code, 200)
        self.assertEqual(json.loads(self.requests[0].content)["messages"], body["messages"])

    async def test_provider_errors_are_http_errors_without_details(self):
        for status, expected in ((401, 503), (402, 503), (403, 503), (429, 429), (500, 502)):
            self.status = status
            self.provider_text = "fake-provider-key private-provider-debug"
            response = await self.request()
            self.assertEqual(response.status_code, expected)
            self.assertNotIn("fake-provider-key", response.text)
            self.assertNotIn("private-provider-debug", response.text)
            self.assertTrue(self.provider_clients[-1].is_closed)

    async def test_missing_key_and_connection_timeout(self):
        with patch.object(settings, "DEEPSEEK_API_KEY", SecretStr("")):
            self.assertEqual((await self.request()).status_code, 503)
        self.assertFalse(self.requests)
        self.provider_error = httpx.ConnectTimeout("private address")
        response = await self.request()
        self.assertEqual(response.status_code, 504)
        self.assertNotIn("private address", response.text)
        self.assertTrue(self.provider_clients[-1].is_closed)

    async def test_invalid_and_truncated_stream_report_error_not_done(self):
        for value in ("data: invalid\n\n", "data: {\"error\":\"private-debug\"}\n\n", upstream_text().replace("data: [DONE]\r\n\r\n", "")):
            self.provider_text = value
            response = await self.request()
            self.assertEqual(response.status_code, 200)
            self.assertIn("event: error", response.text)
            self.assertNotIn("event: done", response.text)
            self.assertNotIn("private-debug", response.text)
            self.assertTrue(self.provider_clients[-1].is_closed)

    async def test_wrong_provider_content_type_is_rejected(self):
        self.provider_headers = {"content-type": "application/json"}
        self.assertEqual((await self.request()).status_code, 502)
        self.assertTrue(self.provider_clients[-1].is_closed)


class StreamLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelling_pending_generation_closes_provider(self):
        waiting = asyncio.Event()

        class BlockedBody(httpx.AsyncByteStream):
            closed = False

            async def __aiter__(self):
                waiting.set()
                await asyncio.Event().wait()
                yield b"never"

            async def aclose(self):
                self.closed = True

        body = BlockedBody()
        client = CLIENT_FACTORY(transport=httpx.MockTransport(lambda request: httpx.Response(
            200, headers={"content-type": "text/event-stream"}, stream=body)))
        response = await client.send(client.build_request("GET", "https://test"), stream=True)
        upstream = deepseek.DeepSeekStream(client, response)
        with patch.object(chat, "open_chat_stream", return_value=upstream):
            result = await chat.chat_stream(ChatRequest(**PROMPT))
        await anext(result.body_iterator)
        task = asyncio.create_task(anext(result.body_iterator))
        await asyncio.wait_for(waiting.wait(), 1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(body.closed)
        self.assertTrue(client.is_closed)

    async def test_total_timeout_is_an_sse_error(self):
        class SlowStream:
            closed = False

            async def events(self):
                await asyncio.sleep(1)
                yield "never"

            async def aclose(self):
                self.closed = True

        upstream = SlowStream()
        with patch.object(chat, "open_chat_stream", return_value=upstream), patch.object(settings, "CHAT_STREAM_TIMEOUT_SECONDS", 0.01):
            result = await chat.chat_stream(ChatRequest(**PROMPT))
            events = [event async for event in result.body_iterator]
        self.assertIn("event: error", "".join(events))
        self.assertTrue(upstream.closed)

    def test_configuration_hides_provider_key(self):
        configured = Settings(ENV="dev", SECRET_KEY="a" * 64, DEEPSEEK_API_KEY="fake-secret")
        self.assertNotIn("fake-secret", repr(configured))
        self.assertNotIn("fake-secret", configured.model_dump_json())
        with self.assertRaises(ValidationError):
            Settings(ENV="dev", SECRET_KEY="a" * 64, DEEPSEEK_MAX_TOKENS=0)

    def test_seed_is_idempotent_and_does_not_assign_users(self):
        fixture = token_tests.TokenIdentityTests()
        fixture.password_hash = "unused"
        fixture.setUp()
        try:
            root = Path(__file__).resolve().parents[1]
            source = (root / "sql/init.sql").read_text()
            source = source.split("-- AI 对话权限与独立角色。", 1)[1].split("COMMIT;", 1)[0] + "COMMIT;"
            with Session(fixture.engine) as db:
                for _ in range(2):
                    for statement in source.replace("public.", "").split(";"):
                        if statement.strip():
                            db.execute(text(statement))
                names = [permission.name for permission in db.scalar(select(Role).where(Role.name == "ai_user")).permissions]
                self.assertEqual(set(names), {"api:chat:use", "menu:chat"})
                self.assertEqual(db.get(User, 1).roles, [])
        finally:
            fixture.tearDown()
