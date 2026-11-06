"""用户 CRUD 回归测试，使用内存数据库验证 HTTP 路由和实际 ORM 写入。

运行：PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -p 'test_user_crud.py'
"""

import contextlib
import io
import json
import unittest
from urllib.parse import urlencode, urlsplit

from fastapi import FastAPI
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session

# 数据库模块的导入会打印连接配置，测试时抑制该输出。
with contextlib.redirect_stdout(io.StringIO()):
    from console_server.api.v1.endpoints import self as self_endpoint
    from console_server.api.v1.endpoints import user as user_endpoint
    from console_server.db import database
    from console_server.model.rbac import Base, Permission, Role, User, user_roles
    from console_server.model.auth_session import AuthSession
    from datetime import datetime, timedelta, timezone
    from uuid import uuid4
    from console_server.utils.auth import (
        issue_session_tokens,
        get_password_hash,
        verify_password,
    )


class AsyncSessionAdapter:
    """将测试用同步 SQLite 会话适配为接口依赖的异步会话方法。"""

    def __init__(self, session: Session):
        self.session = session

    async def execute(self, statement):
        return self.session.execute(statement)

    def add(self, instance):
        self.session.add(instance)

    async def commit(self):
        self.session.commit()

    async def rollback(self):
        self.session.rollback()

    async def refresh(self, instance, attribute_names=None):
        self.session.refresh(instance, attribute_names)

    async def delete(self, instance):
        self.session.delete(instance)


class UserCrudTests(unittest.IsolatedAsyncioTestCase):
    """通过真实 JWT 和权限依赖验证用户生命周期及拒绝路径。"""

    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")

        @event.listens_for(self.engine, "connect")
        def enable_foreign_keys(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")

        Base.metadata.create_all(self.engine)
        with Session(self.engine) as session:
            reader = Role(name="reader", display_name="只读用户", is_active=True)
            reader.permissions = [
                Permission(name="api:user:get", display_name="查询用户")
            ]
            editor = Role(name="editor", display_name="编辑用户", is_active=True)
            editor.permissions = [
                Permission(name="api:user:GET,PUT", display_name="查询编辑用户")
            ]
            roles = [
                Role(name="admin", display_name="管理员", is_active=True,
                     permissions=[Permission(name="api:*", display_name="全局管理")]),
                Role(name="normal", display_name="普通用户", is_active=True),
                reader,
                editor,
            ]
            for user_id, role in enumerate(roles, 1):
                session.add(
                    User(
                        id=user_id,
                        name=role.display_name,
                        email=f"actor{user_id}@example.com",
                        password="fixture-hash",
                        is_active=True,
                        is_editable=True,
                        is_deletable=True,
                        roles=[role],
                    )
                )
            session.commit()

        self.app = FastAPI()
        self.app.include_router(user_endpoint.router, prefix="/api/v1")
        self.app.include_router(self_endpoint.router, prefix="/api/v1")

        async def get_test_db():
            with Session(self.engine, expire_on_commit=False) as session:
                yield AsyncSessionAdapter(session)

        self.app.dependency_overrides[database.get_db] = get_test_db

    def tearDown(self):
        self.engine.dispose()

    async def request(self, method, url, body=None, actor=1, token=None):
        """直接调用 ASGI 应用，保留路由参数、响应模型和鉴权校验。"""
        parsed = urlsplit(url)
        payload = json.dumps(body).encode() if body is not None else b""
        headers = [(b"content-type", b"application/json")]
        if actor is not None or token is not None:
            if token is None:
                with Session(self.engine) as session:
                    user = session.get(User, actor)
                    auth_session = AuthSession(id=uuid4(), user_uuid=user.uuid, persistent=False,
                                               expires_at=datetime.now(timezone.utc) + timedelta(days=1))
                    token = issue_session_tokens(user, auth_session)["access_token"]
                    session.add(auth_session)
                    session.commit()
            headers.append((b"authorization", f"Bearer {token}".encode()))
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": parsed.path,
            "raw_path": parsed.path.encode(),
            "query_string": parsed.query.encode(),
            "root_path": "",
            "headers": headers,
            "client": ("127.0.0.1", 1),
            "server": ("test", 80),
        }
        messages = []

        async def receive():
            return {"type": "http.request", "body": payload, "more_body": False}

        async def send(message):
            messages.append(message)

        await self.app(scope, receive, send)
        code = next(m["status"] for m in messages if m["type"] == "http.response.start")
        response = b"".join(
            m.get("body", b"") for m in messages if m["type"] == "http.response.body"
        )
        return code, json.loads(response)

    async def create(self, **changes):
        body = {
            "name": " 测试用户 ",
            "email": "test@example.com",
            "password": "password123",
            "description": "测试描述",
            "is_active": True,
            **changes,
        }
        code, data = await self.request("POST", "/api/v1/user/create", body)
        self.assertEqual(code, 201, data)
        return data

    async def test_search_combines_filters_before_pagination(self):
        with Session(self.engine) as session:
            session.add_all([
                User(name="Alice", email="alice@team.com", password="fixture", is_active=True),
                User(name="Alicia", email="alicia@team.com", password="fixture", is_active=False),
                User(name="Alice_other", email="other@else.com", password="fixture", is_active=False),
            ])
            session.commit()
        query = urlencode({"name": " ALI ", "email": "TEAM.COM", "is_active": "false", "page_size": 1})
        code, result = await self.request("GET", f"/api/v1/user/list?{query}")
        self.assertEqual(code, 200)
        self.assertEqual([item["name"] for item in result["items"]], ["Alicia"])
        self.assertEqual((result["total"], result["total_pages"]), (1, 1))
        code, result = await self.request("GET", "/api/v1/user/list?name=ali&page=2&page_size=1")
        self.assertEqual((code, result["total"], result["total_pages"]), (200, 3, 3))
        self.assertEqual(result["items"][0]["name"], "Alicia")
        _, empty = await self.request("GET", "/api/v1/user/list?name=missing")
        self.assertEqual((empty["items"], empty["total"], empty["total_pages"]), ([], 0, 0))

    async def test_search_empty_values_keyword_and_literal_wildcards(self):
        with Session(self.engine) as session:
            session.add(User(name="Literal_%", email="literal@example.com", password="fixture", description="Searchable note"))
            session.commit()
        for query in [{"name": "_%"}, {"keyword": "searchable"}]:
            code, result = await self.request("GET", f"/api/v1/user/list?{urlencode(query)}")
            self.assertEqual((code, result["total"]), (200, 1))
            self.assertEqual(result["items"][0]["name"], "Literal_%")
        _, all_users = await self.request("GET", "/api/v1/user/list?name=%20%20&email=")
        self.assertEqual(all_users["total"], 5)
        self.assertTrue(all("password" not in item for item in all_users["items"]))
        for query in ["is_active=invalid", "name=" + "a" * 129, "email=" + "a" * 257]:
            self.assertEqual((await self.request("GET", f"/api/v1/user/list?{query}"))[0], 422)

    async def test_create_read_update_delete(self):
        user = await self.create()
        user_id = user["id"]
        self.assertEqual(user["name"], "测试用户")
        self.assertEqual(user["roles"][0]["name"], "normal")
        self.assertTrue(user["is_editable"] and user["is_deletable"])
        self.assertNotIn("password", user)
        with Session(self.engine) as session:
            password = session.get(User, user_id).password
            self.assertNotEqual(password, "password123")
            self.assertTrue(verify_password("password123", password))

        code, detail = await self.request("GET", f"/api/v1/user/{user_id}")
        self.assertEqual((code, detail["id"]), (200, user_id))
        code, page = await self.request("GET", "/api/v1/user/list?page=2&page_size=3")
        self.assertEqual(code, 200)
        self.assertEqual([item["id"] for item in page["items"]], [4, user_id])
        self.assertEqual((page["total"], page["total_pages"]), (5, 2))

        body = {
            "name": "编辑后用户",
            "email": "changed@example.com",
            "description": None,
            "is_active": True,
        }
        code, updated = await self.request(
            "PUT", f"/api/v1/user/{user_id}/update", body
        )
        self.assertEqual(
            (code, updated["name"], updated["description"]), (200, "编辑后用户", None)
        )
        with Session(self.engine) as session:
            self.assertEqual(session.get(User, user_id).password, password)

        code, _ = await self.request(
            "PUT",
            f"/api/v1/user/{user_id}/update",
            {**body, "password": "newpassword123"},
        )
        self.assertEqual(code, 200)
        with Session(self.engine) as session:
            self.assertTrue(
                verify_password("newpassword123", session.get(User, user_id).password)
            )

        code, _ = await self.request("DELETE", f"/api/v1/user/{user_id}/remove")
        self.assertEqual(code, 200)
        with Session(self.engine) as session:
            self.assertIsNone(session.get(User, user_id))
            self.assertEqual(
                session.scalar(
                    select(func.count())
                    .select_from(user_roles)
                    .where(user_roles.c.user_id == user_id)
                ),
                0,
            )
            self.assertEqual(session.scalar(select(func.count()).select_from(Role)), 4)
        code, _ = await self.request("GET", f"/api/v1/user/{user_id}")
        self.assertEqual(code, 404)

    async def test_duplicate_email_and_invalid_fields(self):
        await self.create()
        valid = {
            "name": "用户",
            "email": "other@example.com",
            "password": "password123",
        }
        for changes, expected in [
            ({"email": "TEST@example.com"}, 400),
            ({"name": "   "}, 422),
            ({"name": "x" * 101}, 422),
            ({"email": "invalid"}, 422),
            ({"description": "x" * 129}, 422),
            ({"password": "short"}, 422),
            ({"password": "密" * 25}, 422),
        ]:
            with self.subTest(changes=changes):
                code, _ = await self.request(
                    "POST", "/api/v1/user/create", {**valid, **changes}
                )
                self.assertEqual(code, expected)
        code, _ = await self.request(
            "POST",
            "/api/v1/user/create",
            {"name": "用户", "email": "other@example.com"},
        )
        self.assertEqual(code, 422)
        code, _ = await self.request(
            "PUT",
            "/api/v1/user/2/update",
            {"name": "用户", "email": "test@example.com"},
        )
        self.assertEqual(code, 400)

    async def test_protected_and_current_accounts(self):
        code, admin = await self.request("GET", "/api/v1/user/1")
        self.assertEqual(code, 200)
        self.assertFalse(admin["is_editable"] or admin["is_deletable"])
        body = {"name": "用户", "email": "actor1@example.com", "is_active": True}
        for method, path, payload in [
            ("PUT", "/api/v1/user/1/update", body),
            ("PUT", "/api/v1/user/1/disable", {"is_active": False}),
            ("DELETE", "/api/v1/user/1/remove", None),
        ]:
            code, _ = await self.request(method, path, payload)
            self.assertEqual(code, 403)
        code, _ = await self.request(
            "PUT",
            "/api/v1/user/4/update",
            {**body, "email": "other@example.com"},
            actor=4,
        )
        self.assertEqual(code, 200)
        code, _ = await self.request(
            "PUT", "/api/v1/user/4/disable", {"is_active": False}, actor=4
        )
        self.assertEqual(code, 400)
        with Session(self.engine) as session:
            editor = session.get(User, 4)
            editor.roles[0].permissions.append(
                Permission(name="api:user:delete", display_name="删除用户")
            )
            session.get(User, 2).is_editable = False
            session.get(User, 2).is_deletable = False
            session.commit()
        code, _ = await self.request("DELETE", "/api/v1/user/4/remove", actor=4)
        self.assertEqual(code, 400)
        code, _ = await self.request("DELETE", "/api/v1/user/2/remove")
        self.assertEqual(code, 403)
        code, _ = await self.request(
            "PUT", "/api/v1/user/2/update", {**body, "email": "actor2@example.com"}
        )
        self.assertEqual(code, 403)

    async def test_permissions_and_inactive_accounts(self):
        for actor, method, path, body, expected in [
            (None, "GET", "/api/v1/user/list", None, 401),
            (2, "GET", "/api/v1/user/list", None, 403),
            (3, "GET", "/api/v1/user/2", None, 200),
            (3, "DELETE", "/api/v1/user/2/remove", None, 403),
            (
                3,
                "PUT",
                "/api/v1/user/2/update",
                {"name": "用户", "email": "actor2@example.com"},
                403,
            ),
            (
                3,
                "POST",
                "/api/v1/user/create",
                {"name": "用户", "email": "new@example.com", "password": "password123"},
                403,
            ),
            (
                4,
                "PUT",
                "/api/v1/user/2/update",
                {"name": "用户", "email": "actor2@example.com"},
                200,
            ),
        ]:
            with self.subTest(actor=actor, method=method):
                code, _ = await self.request(method, path, body, actor=actor)
                self.assertEqual(code, expected)
        code, _ = await self.request(
            "PUT", "/api/v1/user/3/disable", {"is_active": False}
        )
        self.assertEqual(code, 200)
        code, _ = await self.request("GET", "/api/v1/self/current", actor=3)
        self.assertEqual(code, 403)
        with Session(self.engine) as session:
            session.get(User, 4).roles[0].is_active = False
            session.commit()
        code, _ = await self.request("GET", "/api/v1/user/list", actor=4)
        self.assertEqual(code, 403)

    async def test_missing_users_and_default_role(self):
        for method, path, body in [
            ("GET", "/api/v1/user/999", None),
            (
                "PUT",
                "/api/v1/user/999/update",
                {"name": "用户", "email": "missing@example.com"},
            ),
            ("DELETE", "/api/v1/user/999/remove", None),
        ]:
            code, _ = await self.request(method, path, body)
            self.assertEqual(code, 404)
        with Session(self.engine) as session:
            role = session.scalar(select(Role).where(Role.name == "normal"))
            role.is_active = False
            session.commit()
        code, _ = await self.request(
            "POST",
            "/api/v1/user/create",
            {"name": "用户", "email": "new@example.com", "password": "password123"},
        )
        self.assertEqual(code, 400)
        with Session(self.engine) as session:
            session.add(Role(name="user", display_name="默认角色", is_active=True))
            session.commit()
        user = await self.create()
        self.assertEqual(user["roles"][0]["name"], "user")

    async def test_user_roles_replace_and_permissions(self):
        """完整替换支持添加、移除及幂等保存，角色权限随关联实时变化。"""
        for role_ids in ([2, 3, 3], [2, 4], [2, 4], [2]):
            code, user = await self.request(
                "PUT", "/api/v1/user/2/roles", {"role_ids": role_ids}
            )
            self.assertEqual(code, 200, user)
            self.assertEqual({role["id"] for role in user["roles"]}, set(role_ids))
            code, assigned = await self.request("GET", "/api/v1/user/2/roles")
            self.assertEqual(code, 200)
            self.assertEqual({role["id"] for role in assigned}, set(role_ids))
            with Session(self.engine) as session:
                association_count = session.scalar(
                    select(func.count())
                    .select_from(user_roles)
                    .where(user_roles.c.user_id == 2)
                )
                self.assertEqual(association_count, len(set(role_ids)))
                self.assertEqual(
                    session.scalar(select(func.count()).select_from(Role)), 4
                )
                self.assertEqual(
                    session.scalar(select(func.count()).select_from(Permission)), 3
                )
            code, _ = await self.request("GET", "/api/v1/user/list", actor=2)
            self.assertEqual(code, 200 if len(set(role_ids)) > 1 else 403)

    async def test_user_roles_rejected_updates_leave_associations(self):
        """不存在、停用及非法角色不能引起部分更新，基础角色不能移除。"""
        with Session(self.engine) as session:
            session.add(
                Role(id=5, name="inactive", display_name="停用角色", is_active=False)
            )
            session.commit()
        for body, expected in [
            ({"role_ids": [2, 3, 999]}, 400),
            ({"role_ids": [2, 5]}, 400),
            ({"role_ids": [3]}, 400),
            ({"role_ids": []}, 400),
            ({"role_ids": [2, 0]}, 422),
            ({"role_ids": [2, -1]}, 422),
            ({"role_ids": None}, 422),
            ({}, 422),
        ]:
            with self.subTest(body=body):
                code, _ = await self.request("PUT", "/api/v1/user/2/roles", body)
                self.assertEqual(code, expected)
                with Session(self.engine) as session:
                    self.assertEqual(
                        [role.id for role in session.get(User, 2).roles], [2]
                    )
        with Session(self.engine) as session:
            user = session.get(User, 2)
            user.roles.append(session.get(Role, 5))
            session.commit()
        for role_ids in ([2, 5], [2]):
            code, user = await self.request(
                "PUT", "/api/v1/user/2/roles", {"role_ids": role_ids}
            )
            self.assertEqual(code, 200, user)
        # 兼容注册使用的 user 基础角色名称。
        with Session(self.engine) as session:
            session.get(Role, 2).name = "user"
            session.commit()
        code, _ = await self.request("PUT", "/api/v1/user/2/roles", {"role_ids": [3]})
        self.assertEqual(code, 400)

    async def test_user_roles_permissions_and_account_protection(self):
        """角色写入检查编辑权限，保护管理员、当前账号和不可编辑账号。"""
        for actor, user_id, expected in [
            (None, 2, 401),
            (3, 2, 403),
            (1, 1, 403),
            (4, 4, 403),
            (1, 999, 404),
        ]:
            code, _ = await self.request(
                "PUT",
                f"/api/v1/user/{user_id}/roles",
                {"role_ids": [2, 3]},
                actor=actor,
            )
            self.assertEqual(code, expected)
        code, user = await self.request(
            "PUT", "/api/v1/user/2/roles", {"role_ids": [2, 3]}, actor=4
        )
        self.assertEqual(code, 403, user)
        with Session(self.engine) as session:
            session.get(User, 2).is_editable = False
            session.commit()
        for path in ("roles", "assign-roles", "remove-roles"):
            code, _ = await self.request(
                "PUT" if path == "roles" else "POST",
                f"/api/v1/user/2/{path}",
                {"role_ids": [3]},
            )
            self.assertEqual(code, 403)

    async def test_user_roles_legacy_add_remove(self):
        """兼容原追加、移除接口，并使用一致的基础角色保护规则。"""
        for role_ids in ([3, 3], [3]):
            code, _ = await self.request(
                "POST", "/api/v1/user/2/assign-roles", {"role_ids": role_ids}
            )
            self.assertEqual(code, 200)
        code, assigned = await self.request("GET", "/api/v1/user/2/roles")
        self.assertEqual(code, 200)
        self.assertEqual({role["id"] for role in assigned}, {2, 3})
        code, _ = await self.request(
            "POST", "/api/v1/user/2/remove-roles", {"role_ids": [2]}
        )
        self.assertEqual(code, 400)
        code, _ = await self.request(
            "POST", "/api/v1/user/2/remove-roles", {"role_ids": [3]}
        )
        self.assertEqual(code, 200)
        with Session(self.engine) as session:
            self.assertEqual([role.id for role in session.get(User, 2).roles], [2])
        # 没有基础角色的账号可以清空自定义角色集合。
        code, user = await self.request("PUT", "/api/v1/user/3/roles", {"role_ids": []})
        self.assertEqual(code, 200, user)
        self.assertEqual(user["roles"], [])


if __name__ == "__main__":
    unittest.main()
