"""UUID 身份和令牌用途回归；使用真实 JWT、HTTP 路由及隔离数据库。"""

import asyncio
import io
import json
import os
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID, uuid4
from unittest.mock import patch

from fastapi import FastAPI
from jose import jwt
from sqlalchemy import create_engine, delete, event, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateSchema, DropSchema

with redirect_stdout(io.StringIO()):
    import test_user_crud as user_tests
    from console_server.api.auth import auth_router
    from console_server.api.v1.endpoints.self import router as self_router
    from console_server.core.config import settings
    from console_server.db.database import get_db
    from console_server.middleware.auth import AuthMiddleware
    from console_server.model.common import Base
    from console_server.model.rbac import User
    from console_server.model.user_identity import user_identities
    from console_server.model.auth_session import AuthSession
    from console_server.utils.auth import create_access_token, create_refresh_token, get_password_hash, issue_session_tokens
    from console_server.utils import auth as auth_utils


class TokenIdentityTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.password_hash = get_password_hash("password123")

    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        with Session(self.engine) as db:
            db.add_all([
                User(id=1, name="original", email="original@example.com", password=self.password_hash,
                     is_active=True, roles=[]),
                User(id=2, name="other", email="other@example.com", password=self.password_hash,
                     is_active=True, roles=[]),
            ])
            db.commit()
            self.user_uuid = db.get(User, 1).uuid
            self.other_uuid = db.get(User, 2).uuid
        self.app = FastAPI()
        self.app.add_middleware(AuthMiddleware)
        self.app.include_router(auth_router, prefix="/api")
        self.app.include_router(self_router, prefix="/api/v1")

        async def database():
            with Session(self.engine, expire_on_commit=False) as db:
                yield user_tests.AsyncSessionAdapter(db)

        self.app.dependency_overrides[get_db] = database

    def tearDown(self):
        self.engine.dispose()

    async def request(self, method, path, *, token=None, cookie=None, body=None, extra_headers=()):
        parsed = urlsplit(path)
        payload = json.dumps(body).encode() if body is not None else b""
        headers = [(b"content-type", b"application/json"), (b"x-csrf-protection", b"1")]
        if token is not None:
            headers.append((b"authorization", f"Bearer {token}".encode()))
        if cookie is not None:
            headers.append((b"cookie", f"refresh_token={cookie}".encode()))
        headers = list({**dict(headers), **dict(extra_headers)}.items())
        scope = {
            "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
            "method": method, "scheme": "https", "path": parsed.path,
            "raw_path": parsed.path.encode(), "query_string": parsed.query.encode(),
            "root_path": "", "headers": headers, "client": ("127.0.0.1", 1),
            "server": ("test", 443),
        }
        messages = []
        delivered = False
        finished = asyncio.Event()

        async def receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": payload, "more_body": False}
            await finished.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            messages.append(message)
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                finished.set()

        await asyncio.wait_for(self.app(scope, receive, send), 3)
        start = next(message for message in messages if message["type"] == "http.response.start")
        body_bytes = b"".join(message.get("body", b"") for message in messages
                              if message["type"] == "http.response.body")
        return start["status"], json.loads(body_bytes), dict(start["headers"])

    def tokens(self, *, persistent=False):
        with Session(self.engine) as db:
            session = AuthSession(id=uuid4(), user_uuid=self.user_uuid, persistent=persistent,
                                  expires_at=datetime.now(timezone.utc) + timedelta(days=30))
            tokens = issue_session_tokens(db.get(User, 1), session)
            db.add(session)
            db.commit()
            return tokens

    def sign(self, **changes):
        payload = jwt.get_unverified_claims(self.tokens()["access_token"])
        payload.update(changes)
        return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)

    async def test_login_issues_uuid_and_distinct_token_uses(self):
        code, body, headers = await self.request("POST", "/api/auth/login", body={
            "email": "original@example.com", "password": "password123",
        })
        self.assertEqual(code, 200)
        cookies = SimpleCookie()
        cookies.load(headers[b"set-cookie"].decode())
        access = jwt.get_unverified_claims(body["access_token"])
        refresh = jwt.get_unverified_claims(cookies["refresh_token"].value)
        self.assertEqual((access["sub"], refresh["sub"]), (str(self.user_uuid), str(self.user_uuid)))
        self.assertEqual((access["token_use"], refresh["token_use"]), ("access", "refresh"))
        self.assertTrue(cookies["refresh_token"]["httponly"])
        self.assertEqual((await self.request("GET", "/api/v1/self/current",
                                            token=body["access_token"]))[0], 200)

    async def test_refresh_rejects_access_and_business_rejects_refresh(self):
        access = self.tokens()["access_token"]
        refresh = self.tokens()["refresh_token"]
        self.assertEqual((await self.request("GET", "/api/v1/self/current", token=refresh))[0], 401)
        self.assertEqual((await self.request("POST", "/api/auth/logout", token=refresh))[0], 200)
        self.assertEqual((await self.request("POST", "/api/auth/refresh", cookie=access))[0], 401)

    async def test_valid_refresh_keeps_uuid_and_returns_access_token(self):
        # 使用与新刷新令牌不同的过期时间，独立验证本次身份及用途变更。
        refresh = self.tokens()["refresh_token"]
        code, body, headers = await self.request("POST", "/api/auth/refresh", cookie=refresh)
        self.assertEqual(code, 200)
        claims = jwt.get_unverified_claims(body["access_token"])
        self.assertEqual((claims["sub"], claims["token_use"]), (str(self.user_uuid), "access"))
        cookies = SimpleCookie()
        cookies.load(headers[b"set-cookie"].decode())
        replacement = cookies["refresh_token"].value
        self.assertEqual(jwt.get_unverified_claims(replacement)["token_use"], "refresh")
        self.assertEqual((await self.request("POST", "/api/auth/refresh", cookie=refresh))[0], 401)
        self.assertEqual((await self.request("GET", "/api/v1/self/current",
                                            token=body["access_token"]))[0], 401)

    async def test_old_email_and_untyped_uuid_tokens_are_rejected(self):
        payloads = [
            {"sub": "original@example.com", "exp": datetime.now(timezone.utc) + timedelta(days=30)},
            {"sub": str(self.user_uuid), "exp": datetime.now(timezone.utc) + timedelta(days=30)},
            {"sub": "original@example.com", "token_use": "access",
             "exp": datetime.now(timezone.utc) + timedelta(days=30)},
        ]
        for payload in payloads:
            with self.subTest(payload=payload):
                token = jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
                self.assertEqual((await self.request("GET", "/api/v1/self/current", token=token))[0], 401)
                self.assertEqual((await self.request("POST", "/api/auth/refresh", cookie=token))[0], 401)

    async def test_missing_or_invalid_claims_and_signature_are_rejected(self):
        valid = jwt.get_unverified_claims(self.tokens()["access_token"])
        payloads = [{key: value for key, value in valid.items() if key != missing}
                    for missing in ("sub", "exp", "token_use", "sid", "jti")]
        payloads += [{**valid, "sub": "invalid"}, {**valid, "sub": 1},
                     {**valid, "sub": None}, {**valid, "sub": []},
                     {**valid, "exp": None}, {**valid, "exp": []},
                     {**valid, "exp": {}}, {**valid, "exp": "invalid"},
                     {**valid, "token_use": "unknown"},
                     {**valid, "exp": datetime.now(timezone.utc) - timedelta(minutes=1)}]
        tokens = [jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
                  for payload in payloads]
        tokens += [jwt.encode(valid, "different-signing-key", algorithm=settings.ALGORITHM)]
        for token in tokens:
            with self.subTest(claims=jwt.get_unverified_claims(token)):
                self.assertEqual((await self.request("GET", "/api/v1/self/current", token=token))[0], 401)

        for invalid_exp in (None, [], {}, "invalid"):
            token = self.sign(token_use="refresh", exp=invalid_exp)
            self.assertEqual((await self.request("POST", "/api/auth/refresh", cookie=token))[0], 401)

    async def test_email_change_and_reuse_do_not_change_token_identity(self):
        access = self.tokens()["access_token"]
        refresh = self.tokens()["refresh_token"]
        with Session(self.engine) as db:
            db.get(User, 1).email = "moved@example.com"
            db.commit()
            db.get(User, 2).email = "original@example.com"
            db.commit()
        code, body, _ = await self.request("GET", "/api/v1/self/current", token=access)
        self.assertEqual((code, body["id"], body["email"]), (200, 1, "moved@example.com"))
        code, body, _ = await self.request("POST", "/api/auth/refresh", cookie=refresh)
        self.assertEqual(code, 200)
        self.assertEqual(jwt.get_unverified_claims(body["access_token"])["sub"], str(self.user_uuid))

    async def test_deleted_account_cannot_be_recreated_with_old_identity(self):
        access = self.tokens()["access_token"]
        refresh = self.tokens()["refresh_token"]
        with Session(self.engine) as db:
            db.execute(delete(User).where(User.id == 1))
            db.commit()
            db.add(User(id=1, name="replacement", email="original@example.com",
                        password=self.password_hash, is_active=True, roles=[]))
            db.commit()
            self.assertNotEqual(db.get(User, 1).uuid, self.user_uuid)
            self.assertIsNotNone(db.scalar(select(user_identities.c.uuid).where(
                user_identities.c.uuid == self.user_uuid)))
        self.assertEqual((await self.request("GET", "/api/v1/self/current", token=access))[0], 401)
        self.assertEqual((await self.request("POST", "/api/auth/refresh", cookie=refresh))[0], 401)

    async def test_uuid_is_immutable_unique_and_cannot_be_reused(self):
        self.assertIsInstance(self.user_uuid, UUID)
        self.assertEqual(self.user_uuid.version, 4)
        with Session(self.engine) as db:
            with self.assertRaises(IntegrityError):
                db.execute(update(User).where(User.id == 1).values(uuid=uuid4()))
            db.rollback()
            db.get(User, 1).uuid = uuid4()
            with self.assertRaises(IntegrityError):
                db.commit()
            db.rollback()
            db.add(User(uuid=self.other_uuid, name="duplicate", email="duplicate@example.com",
                        password=self.password_hash, roles=[]))
            with self.assertRaises(IntegrityError):
                db.commit()
            db.rollback()
            db.execute(delete(User).where(User.id == 1))
            db.commit()
            db.add(User(uuid=self.user_uuid, name="reused", email="reuse@example.com",
                        password=self.password_hash, roles=[]))
            with self.assertRaises(IntegrityError):
                db.commit()
            db.rollback()
            for statement in (delete(user_identities), update(user_identities).values(uuid=uuid4())):
                with self.assertRaises(IntegrityError):
                    db.execute(statement)
                db.rollback()

    async def test_disabled_and_revoked_tokens_still_fail(self):
        access = self.tokens()["access_token"]
        refresh = self.tokens()["refresh_token"]
        with Session(self.engine) as db:
            db.get(User, 1).is_active = False
            db.commit()
        self.assertEqual((await self.request("GET", "/api/v1/self/current", token=access))[0], 403)
        self.assertEqual((await self.request("POST", "/api/auth/refresh", cookie=refresh))[0], 401)
        with Session(self.engine) as db:
            db.get(User, 1).is_active = True
            db.commit()
        self.assertEqual((await self.request("POST", "/api/auth/logout", token=access, cookie=refresh))[0], 200)
        self.assertEqual((await self.request("GET", "/api/v1/self/current", token=access))[0], 401)
        self.assertEqual((await self.request("POST", "/api/auth/refresh", cookie=refresh))[0], 401)

    async def login_pair(self, *, remember=False):
        code, body, headers = await self.request("POST", "/api/auth/login", body={
            "email": "original@example.com", "password": "password123", "rememberMe": remember,
        })
        self.assertEqual(code, 200)
        cookies = SimpleCookie()
        cookies.load(headers[b"set-cookie"].decode())
        return body["access_token"], cookies["refresh_token"], headers

    async def test_jti_is_random_unique_and_cannot_be_overridden(self):
        sid = str(uuid4())
        data = {"sub": str(self.user_uuid), "sid": sid, "jti": str(uuid4())}
        tokens = [factory(data) for factory in (create_access_token, create_refresh_token) for _ in range(100)]
        claims = [jwt.get_unverified_claims(token) for token in tokens]
        ids = [claim["jti"] for claim in claims]
        self.assertEqual(len(set(ids)), 200)
        self.assertNotIn(data["jti"], ids)
        self.assertTrue(all(UUID(jti).version == 4 for jti in ids))
        for field in ("jti", "sid"):
            for value in (None, "invalid", [], 1):
                token = self.sign(**{field: value})
                self.assertEqual((await self.request("GET", "/api/v1/self/current", token=token))[0], 401)

    async def test_remember_me_only_controls_cookie_persistence(self):
        for remember in (False, True):
            _, cookie, headers = await self.login_pair(remember=remember)
            self.assertEqual(bool(cookie["max-age"]), remember)
            self.assertTrue(cookie["httponly"])
            self.assertEqual(cookie["samesite"], "lax")
            self.assertEqual(headers[b"cache-control"], b"no-store")
            code, _, renewed = await self.request("POST", "/api/auth/refresh", cookie=cookie.value)
            self.assertEqual(code, 200)
            parsed = SimpleCookie()
            parsed.load(renewed[b"set-cookie"].decode())
            self.assertEqual(bool(parsed["refresh_token"]["max-age"]), remember)

    async def test_rotation_has_unique_jti_and_fixed_absolute_expiry(self):
        access, cookie, _ = await self.login_pair()
        original = jwt.get_unverified_claims(cookie.value)
        code, body, headers = await self.request("POST", "/api/auth/refresh", cookie=cookie.value)
        self.assertEqual(code, 200)
        replacement = SimpleCookie()
        replacement.load(headers[b"set-cookie"].decode())
        claims = jwt.get_unverified_claims(replacement["refresh_token"].value)
        self.assertEqual(claims["sid"], original["sid"])
        self.assertEqual(claims["exp"], original["exp"])
        self.assertNotEqual(claims["jti"], original["jti"])
        # 刷新不会立即撤销本会话里先前签发、尚未过期的访问令牌。
        self.assertEqual((await self.request("GET", "/api/v1/self/current", token=access))[0], 200)
        # 重放旧刷新凭证将整个会话撤销。
        self.assertEqual((await self.request("POST", "/api/auth/refresh", cookie=cookie.value))[0], 401)
        self.assertEqual((await self.request("GET", "/api/v1/self/current", token=body["access_token"]))[0], 401)
        self.assertEqual((await self.request("POST", "/api/auth/refresh", cookie=replacement["refresh_token"].value))[0], 401)

    async def test_logout_revokes_entire_session_and_is_idempotent(self):
        access, cookie, _ = await self.login_pair()
        other_access, other_cookie, _ = await self.login_pair()
        code, body, _ = await self.request("POST", "/api/auth/refresh", cookie=cookie.value)
        self.assertEqual(code, 200)
        self.assertEqual((await self.request("POST", "/api/auth/logout", token=access))[0], 200)
        for token in (access, body["access_token"]):
            self.assertEqual((await self.request("GET", "/api/v1/self/current", token=token))[0], 401)
        self.assertEqual((await self.request("POST", "/api/auth/logout", token=access))[0], 200)
        self.assertEqual((await self.request("POST", "/api/auth/logout"))[0], 200)
        self.assertEqual((await self.request("GET", "/api/v1/self/current", token=other_access))[0], 200)
        self.assertEqual((await self.request("POST", "/api/auth/refresh", cookie=other_cookie.value))[0], 200)

    async def test_expired_access_and_cookie_only_can_logout(self):
        for cookie_only in (False, True):
            access, cookie, _ = await self.login_pair()
            claims = jwt.get_unverified_claims(access)
            claims["exp"] = int(datetime.now(timezone.utc).timestamp()) - 10
            expired = jwt.encode(claims, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
            code, _, headers = await self.request("POST", "/api/auth/logout",
                token=None if cookie_only else expired, cookie=cookie.value if cookie_only else None)
            self.assertEqual(code, 200)
            self.assertIn(b"Max-Age=0", headers[b"set-cookie"])
            self.assertEqual((await self.request("GET", "/api/v1/self/current", token=access))[0], 401)
            self.assertEqual((await self.request("POST", "/api/auth/refresh", cookie=cookie.value))[0], 401)

    async def test_password_change_and_disable_reenable_revoke_all_sessions(self):
        for changes in ({"password": "new-hash"}, {"is_active": False}):
            pairs = [self.tokens() for _ in range(2)]
            with Session(self.engine) as db:
                db.execute(update(User).where(User.id == 1).values(**changes))
                db.commit()
                db.execute(update(User).where(User.id == 1).values(is_active=True))
                db.commit()
            for pair in pairs:
                self.assertEqual((await self.request("GET", "/api/v1/self/current", token=pair["access_token"]))[0], 401)
                self.assertEqual((await self.request("POST", "/api/auth/refresh", cookie=pair["refresh_token"]))[0], 401)

    async def test_logout_all_rejects_refresh_and_revokes_only_current_account(self):
        first = self.tokens()
        second = self.tokens()
        other = self.sign(sub=str(self.other_uuid))
        self.assertEqual((await self.request("POST", "/api/auth/logout-all", token=first["refresh_token"]))[0], 401)
        self.assertEqual((await self.request("POST", "/api/auth/logout-all", token=first["access_token"]))[0], 200)
        for pair in (first, second):
            self.assertEqual((await self.request("GET", "/api/v1/self/current", token=pair["access_token"]))[0], 401)
        # 不能把其他用户 UUID 与本用户 sid 拼接后用于鉴权。
        self.assertEqual((await self.request("GET", "/api/v1/self/current", token=other))[0], 401)

    async def test_cookie_mutations_use_post_and_clear_invalid_cookie(self):
        for path in ("/api/auth/refresh", "/api/auth/logout"):
            self.assertEqual((await self.request("GET", path))[0], 405)
        code, _, headers = await self.request("POST", "/api/auth/refresh", cookie="invalid")
        self.assertEqual(code, 401)
        self.assertIn(b"Max-Age=0", headers[b"set-cookie"])
        self.assertEqual(headers[b"cache-control"], b"no-store")

    async def test_form_and_cross_site_cannot_mutate_cookie_sessions(self):
        for path in ("/api/auth/refresh", "/api/auth/logout"):
            code, _, _ = await self.request("POST", path, extra_headers=[(b"sec-fetch-site", b"cross-site")])
            self.assertEqual(code, 403)
        # 缺少 JSON Content-Type 的 HTML 表单不被当作会话操作。
        from console_server.api.auth import validate_cookie_request
        from starlette.requests import Request
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as failure:
            validate_cookie_request(Request({"type": "http", "headers": [(b"content-type", b"application/x-www-form-urlencoded")]}))
        self.assertEqual(failure.exception.status_code, 415)

    async def test_csrf_rejects_missing_header_and_untrusted_same_site_without_rotating(self):
        _, cookie, _ = await self.login_pair()
        invalid_headers = [
            [(b"x-csrf-protection", b"")],
            [(b"origin", b"https://untrusted.test"), (b"sec-fetch-site", b"same-site")],
            [(b"origin", b"https://test.evil.example")],
            [(b"origin", b"https://test:444")],
            [(b"origin", b"null")],
            [(b"referer", b"https://evil.example/page")],
            [(b"sec-fetch-site", b"same-site")],
        ]
        for headers in invalid_headers:
            for path in ("/api/auth/refresh", "/api/auth/logout"):
                code, _, response_headers = await self.request("POST", path, cookie=cookie.value,
                                                              extra_headers=headers)
                self.assertEqual(code, 403)
                self.assertNotIn(b"set-cookie", response_headers)
                self.assertEqual(response_headers[b"cache-control"], b"no-store")
        self.assertEqual((await self.request("POST", "/api/auth/refresh", cookie=cookie.value,
                                           extra_headers=[(b"origin", b"https://test:443")]))[0], 200)

    async def test_csrf_trusted_proxy_origin_and_referer_fallback(self):
        for headers in ([ (b"origin", b"https://console.example") ],
                        [ (b"referer", b"https://console.example/account?tab=security") ]):
            _, cookie, _ = await self.login_pair()
            with patch.object(settings, "AUTH_ALLOWED_ORIGINS", ["https://console.example"]):
                self.assertEqual((await self.request("POST", "/api/auth/refresh", cookie=cookie.value,
                                                    extra_headers=headers))[0], 200)
        _, cookie, _ = await self.login_pair()
        with patch.object(settings, "AUTH_ALLOWED_ORIGINS", ["https://console.example"]):
            self.assertEqual((await self.request("POST", "/api/auth/logout", cookie=cookie.value,
                                                extra_headers=[(b"origin", b"https://test")]))[0], 403)

    async def test_auth_failures_use_401_and_all_auth_responses_disable_caching(self):
        for path, method, body, expected in (
            ("/api/auth/login", "POST", {"email": "original@example.com", "password": "wrong"}, 401),
            ("/api/auth/refresh", "POST", None, 401),
            ("/api/auth/refresh", "GET", None, 405),
            ("/api/auth/logout", "POST", None, 200),
        ):
            code, _, headers = await self.request(method, path, body=body)
            self.assertEqual(code, expected)
            self.assertEqual(headers[b"cache-control"], b"no-store")
            self.assertEqual(headers[b"pragma"], b"no-cache")
        for path in ("/api/auth/login", "/api/auth/logout-all"):
            access = self.tokens()["access_token"]
            code, _, _ = await self.request("POST", path, token=access,
                body={"email": "original@example.com", "password": "password123"},
                extra_headers=[(b"origin", b"https://evil.example")])
            self.assertEqual(code, 403)

    async def test_request_verifies_signature_once_and_combines_database_queries(self):
        access = self.tokens()["access_token"]
        statements = []
        def record(_connection, _cursor, statement, _parameters, _context, _many):
            statements.append(statement)
        event.listen(self.engine, "before_cursor_execute", record)
        try:
            with patch.object(auth_utils, "decode_token", wraps=auth_utils.decode_token) as decode:
                self.assertEqual((await self.request("GET", "/api/v1/self/current", token=access))[0], 200)
                self.assertEqual(decode.call_count, 1)
            # 一个用户/会话/黑名单查询，另一个查询预加载空的角色集合。
            self.assertEqual(len(statements), 2, statements)
            self.assertIn("auth_sessions", statements[0])
            self.assertIn("token_blacklist", statements[0])
            statements.clear()
            self.assertEqual((await self.request("GET", "/api/v1/self/current", token="invalid"))[0], 401)
            self.assertEqual(statements, [])
        finally:
            event.remove(self.engine, "before_cursor_execute", record)

    async def test_payload_cache_never_reuses_another_token_or_use(self):
        from starlette.requests import Request
        request = Request({"type": "http", "headers": []})
        first, second = self.tokens(), self.tokens()
        with patch.object(auth_utils, "decode_token", wraps=auth_utils.decode_token) as decode:
            auth_utils.verified_token_payload(first["access_token"], "access", request)
            auth_utils.verified_token_payload(first["access_token"], "access", request)
            auth_utils.verified_token_payload(second["access_token"], "access", request)
            auth_utils.verified_token_payload(second["refresh_token"], "refresh", request)
            self.assertEqual(decode.call_count, 3)

    async def test_refresh_at_later_date_still_obeys_absolute_session_deadline(self):
        _, cookie, _ = await self.login_pair()
        original = jwt.get_unverified_claims(cookie.value)
        from datetime import datetime as RealDatetime
        class Clock(RealDatetime):
            @classmethod
            def now(cls, tz=None):
                return RealDatetime.fromtimestamp(original["exp"] - 20, timezone.utc)
        with patch.object(auth_utils, "datetime", Clock):
            code, body, headers = await self.request("POST", "/api/auth/refresh", cookie=cookie.value)
        self.assertEqual(code, 200)
        replacement = SimpleCookie()
        replacement.load(headers[b"set-cookie"].decode())
        self.assertEqual(jwt.get_unverified_claims(replacement["refresh_token"].value)["exp"], original["exp"])
        self.assertLessEqual(jwt.get_unverified_claims(body["access_token"])["exp"], original["exp"])
        class ExpiredClock(RealDatetime):
            @classmethod
            def now(cls, tz=None):
                return RealDatetime.fromtimestamp(original["exp"] + 1, timezone.utc)
        with patch.object(auth_utils, "datetime", ExpiredClock):
            self.assertEqual((await self.request("POST", "/api/auth/refresh",
                cookie=replacement["refresh_token"].value))[0], 401)


@unittest.skipUnless(os.getenv("CONSOLE_TEST_DATABASE_URL"), "未配置独立 PostgreSQL 测试连接")
class PostgreSQLUuidTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.schema = "uuid_test_" + uuid4().hex
        self.engine = create_async_engine(os.environ["CONSOLE_TEST_DATABASE_URL"], echo=False)
        async with self.engine.begin() as connection:
            await connection.execute(CreateSchema(self.schema))

    async def asyncTearDown(self):
        try:
            async with self.engine.begin() as connection:
                await connection.execute(DropSchema(self.schema, cascade=True))
        finally:
            await self.engine.dispose()

    async def test_orm_creation_installs_guards_in_translated_schema(self):
        translated = self.engine.execution_options(schema_translate_map={None: self.schema})
        async with translated.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
            await connection.execute(User.__table__.insert().values(
                id=1, name="ORM", email="orm@example.com", password="hash"))
            identities = (await connection.execute(select(user_identities.c.uuid))).all()
            self.assertEqual(len(identities), 1)
            with self.assertRaises(IntegrityError):
                await connection.execute(update(User).where(User.id == 1).values(uuid=uuid4()))
            await connection.rollback()

    async def test_initialization_sql_installs_identity_guards(self):
        source = (Path(__file__).resolve().parents[1] / "sql" / "init.sql").read_text()
        users_ddl = source.split('CREATE TABLE "public"."users" (', 1)[1].split(
            'ALTER TABLE "public"."users" OWNER', 1)[0]
        users_ddl = 'CREATE TABLE "public"."users" (' + users_ddl
        users_ddl = users_ddl.replace('"public".', f'"{self.schema}".').replace(
            "'users_id_seq'", f"'{self.schema}.users_id_seq'")
        identity_ddl = source.split("-- 用户鉴权 UUID 永久保留，不允许修改或复用。", 1)[1].split("-- 登录会话：", 1)[0].strip() + "\n"
        identity_ddl = identity_ddl.replace("BEGIN;", "", 1).removesuffix("COMMIT;\n")
        identity_ddl = identity_ddl.replace('"public".', f'"{self.schema}".').replace(
            "public.", f"{self.schema}.")
        async with self.engine.begin() as connection:
            await connection.execute(text(f"CREATE SEQUENCE {self.schema}.users_id_seq"))
            await connection.execute(text(users_ddl))
            raw = await connection.get_raw_connection()
            await raw.driver_connection.execute(identity_ddl)
            session_ddl = source.split("-- 登录会话：", 1)[1].split("BEGIN;", 1)[1].rsplit("COMMIT;", 1)[0]
            session_ddl = session_ddl.replace('"public".', f'"{self.schema}".')
            await connection.execute(text(f'SET LOCAL search_path TO "{self.schema}"'))
            await raw.driver_connection.execute(session_ddl)
            original = (await connection.execute(text(
                f"INSERT INTO {self.schema}.users (name, email) "
                "VALUES ('SQL', 'sql@example.com') RETURNING uuid"))).scalar_one()
            self.assertEqual(original.version, 4)
            reserved = (await connection.execute(text(
                f"SELECT uuid FROM {self.schema}.user_identities"))).scalar_one()
            self.assertEqual(original, reserved)
            session_id = uuid4()
            await connection.execute(text(f"INSERT INTO {self.schema}.auth_sessions "
                "(id, user_uuid, refresh_jti, expires_at) VALUES (:sid, :user_uuid, :jti, CURRENT_TIMESTAMP + INTERVAL '1 day')"),
                {"sid": session_id, "user_uuid": original, "jti": uuid4()})
            await connection.execute(text(f"UPDATE {self.schema}.users SET password = 'reset-hash'"))
            revoked = (await connection.execute(text(f"SELECT revoked_at FROM {self.schema}.auth_sessions WHERE id = :sid"),
                                               {"sid": session_id})).scalar_one()
            self.assertIsNotNone(revoked)
        async with self.engine.connect() as connection:
            with self.assertRaises(IntegrityError):
                await connection.execute(text(
                    f"UPDATE {self.schema}.users SET uuid = gen_random_uuid()"))
            await connection.rollback()
