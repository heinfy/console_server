"""权限 CRUD 接口回归测试，使用内存 SQLite 验证业务逻辑和真实 SQL。

运行命令：PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
测试通过 ASGI 调用实际路由，无需连接开发数据库或安装额外测试依赖。
"""

import io
import json
from pathlib import Path
import re
import unittest
from contextlib import redirect_stdout
from unittest.mock import AsyncMock, patch
from urllib.parse import urlencode

from fastapi import FastAPI
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

# 数据库模块会输出连接地址，测试导入时避免将环境配置写入测试日志。
with redirect_stdout(io.StringIO()):
    from console_server.api.v1.endpoints.permission import router
    from console_server.api.v1.endpoints.role import router as role_router
    from console_server.db import database
    from console_server.model.common import Base
    from console_server.model.rbac import AuthorizationAudit, Permission, Role, User, role_permissions
    from console_server.utils.auth import get_current_user


class _DatabaseSession:
    """为同步 SQLite 会话提供接口所需的异步调用形式。"""

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

    async def refresh(self, instance):
        self.session.refresh(instance)

    async def delete(self, instance):
        self.session.delete(instance)


class PermissionApiTestCase(unittest.IsolatedAsyncioTestCase):
    """验证权限生命周期、输入校验、操作授权和事务一致性。"""

    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")

        @event.listens_for(self.engine, "connect")
        def enable_foreign_keys(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")

        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine, expire_on_commit=False)
        self.db = _DatabaseSession(self.session)
        self.app = FastAPI()
        self.app.include_router(router, prefix="/api/v1")
        self.app.include_router(role_router, prefix="/api/v1")
        self.set_actor("admin")

        async def get_test_db():
            yield self.db

        async def get_test_user():
            return self.actor

        self.app.dependency_overrides[database.get_db] = get_test_db
        self.app.dependency_overrides[get_current_user] = get_test_user

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def set_actor(self, role_name="member", permissions=(), active=True):
        """只替换登录用户，保留真实 require_permission 授权逻辑。"""
        if not permissions and role_name == "admin":
            permissions = ("api:*",)
        elif not permissions and role_name.endswith("_admin"):
            permissions = (f"api:{role_name[:-6]}:*",)
        role = Role(name=role_name, display_name=role_name, is_active=True)
        role.permissions = [Permission(name=name) for name in permissions]
        self.actor = User(
            id=100, name="test", email="test@example.com", is_active=active
        )
        self.actor.roles = [role]

    def create_record(self, name="api:test:get", editable=True, deletable=True):
        permission = Permission(
            name=name,
            display_name="测试权限",
            description="原始描述",
            is_editable=editable,
            is_deletable=deletable,
        )
        self.session.add(permission)
        self.session.commit()
        return permission

    async def request(self, method, path, body=None, headers=None, query="", resource="permission"):
        """通过 ASGI 请求接口，保留 FastAPI 参数验证和响应序列化。"""
        messages = []
        payload = json.dumps(body).encode() if body is not None else b""
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": f"/api/v1/{resource}{path}",
            "raw_path": f"/api/v1/{resource}{path}".encode(),
            "query_string": query.encode(),
            "root_path": "",
            "headers": [(b"content-type", b"application/json"), *(headers or [])],
            "server": ("testserver", 80),
            "client": ("127.0.0.1", 12345),
        }

        async def receive():
            return {"type": "http.request", "body": payload, "more_body": False}

        async def send(message):
            messages.append(message)

        await self.app(scope, receive, send)
        status_code = next(
            message["status"]
            for message in messages
            if message["type"] == "http.response.start"
        )
        response_body = b"".join(
            message.get("body", b"")
            for message in messages
            if message["type"] == "http.response.body"
        )
        return status_code, json.loads(response_body)

    async def test_search_combines_conditions_and_counts_filtered_pages(self):
        for name, editable, deletable in [
            ("api:team:first", True, False),
            ("api:team:second", True, False),
            ("api:team:protected", False, False),
            ("api:other:get", True, True),
        ]:
            record = self.create_record(name, editable, deletable)
            record.display_name = "Team permission"
            self.session.commit()
        search = {"name": " TEAM ", "display_name": "PERMISSION", "is_editable": "true", "is_deletable": "false"}
        code, result = await self.request("GET", "/page", query=urlencode({**search, "page": 2, "page_size": 1}))
        self.assertEqual((code, result["total"], result["total_pages"]), (200, 2, 2))
        self.assertEqual([item["name"] for item in result["items"]], ["api:team:second"])
        _, all_matches = await self.request("GET", "/list", query=urlencode(search))
        self.assertEqual([item["name"] for item in all_matches], ["api:team:first", "api:team:second"])
        _, empty = await self.request("GET", "/page", query="name=missing")
        self.assertEqual((empty["items"], empty["total"], empty["total_pages"]), ([], 0, 0))
        _, all_records = await self.request("GET", "/page", query="name=%20&keyword=")
        self.assertEqual(all_records["total"], 4)
        _, legacy = await self.request("GET", "/list")
        self.assertEqual(len(legacy), 4)

    async def test_search_keyword_literal_wildcards_and_validation(self):
        record = self.create_record("api:literal_%:get")
        record.description = "Unique Needle"
        self.session.commit()
        self.create_record("api:other:get")
        for search in [{"name": "_%"}, {"keyword": " NEEDLE "}, {"keyword": "literal"}]:
            code, result = await self.request("GET", "/page", query=urlencode(search))
            self.assertEqual((code, result["total"]), (200, 1))
            self.assertEqual(result["items"][0]["id"], record.id)
        for query in ["is_editable=invalid", "is_deletable=invalid", "page=0", "page_size=101", "keyword=" + "a" * 257]:
            self.assertEqual((await self.request("GET", "/page", query=query))[0], 422)

    async def test_crud_lifecycle(self):
        status_code, empty = await self.request("GET", "/list")
        self.assertEqual((status_code, empty), (200, []))
        status_code, created = await self.request(
            "POST",
            "/create",
            {
                "name": "  api:test:get  ",
                "display_name": " 测试权限 ",
                "description": " 测试说明 ",
                "is_editable": True,
                "is_deletable": True,
            },
        )
        self.assertEqual(status_code, 200)
        self.assertEqual(created["name"], "api:test:get")
        self.assertTrue(created["is_editable"])
        self.assertTrue(created["is_deletable"])
        self.assertIn("created_at", created)
        permission_id = created["id"]
        status_code, detail = await self.request("GET", f"/{permission_id}")
        self.assertEqual((status_code, detail), (200, created))
        status_code, updated = await self.request(
            "PUT",
            f"/{permission_id}/update",
            {
                "name": "api:test:get",
                "display_name": "更新权限",
                "description": None,
            },
        )
        self.assertEqual((status_code, updated), (200, {"detail": "ok"}))
        _, detail = await self.request("GET", f"/{permission_id}")
        self.assertEqual(detail["name"], "api:test:get")
        self.assertIsNone(detail["description"])
        self.assertTrue(detail["is_editable"])
        self.assertTrue(detail["is_deletable"])
        status_code, removed = await self.request("DELETE", f"/{permission_id}/remove")
        self.assertEqual((status_code, removed), (200, {"detail": "ok"}))
        self.assertEqual((await self.request("GET", "/list"))[1], [])
        self.assertEqual((await self.request("GET", f"/{permission_id}"))[0], 404)

    async def test_protected_records_and_flag_updates(self):
        protected = self.create_record(editable=False, deletable=False)
        self.assertEqual(
            (
                await self.request(
                    "PUT",
                    f"/{protected.id}/update",
                    {
                        "name": protected.name,
                        "display_name": "不能修改",
                        "is_editable": True,
                    },
                )
            )[0],
            403,
        )
        self.assertEqual(
            (await self.request("DELETE", f"/{protected.id}/remove"))[0], 403
        )
        self.assertEqual(protected.display_name, "测试权限")
        editable = self.create_record("api:editable:get")
        self.assertEqual(
            (
                await self.request(
                    "PUT",
                    f"/{editable.id}/update",
                    {
                        "name": editable.name,
                        "display_name": "锁定权限",
                        "is_editable": False,
                        "is_deletable": False,
                    },
                )
            )[0],
            200,
        )
        self.assertFalse(editable.is_editable)
        self.assertFalse(editable.is_deletable)
        self.assertEqual(
            (await self.request("DELETE", f"/{editable.id}/remove"))[0], 403
        )

    async def test_duplicate_codes_do_not_change_existing_records(self):
        first = self.create_record("api:first:get")
        second = self.create_record("api:second:get")
        self.assertEqual(
            (
                await self.request(
                    "POST",
                    "/create",
                    {
                        "name": first.name,
                        "display_name": "重复权限",
                    },
                )
            )[0],
            400,
        )
        self.assertEqual(
            (
                await self.request(
                    "PUT",
                    f"/{second.id}/update",
                    {
                        "name": first.name,
                        "display_name": "重复权限",
                    },
                )
            )[0],
            400,
        )
        self.assertEqual(second.name, "api:second:get")
        self.assertEqual(
            (
                await self.request(
                    "PUT",
                    f"/{second.id}/update",
                    {
                        "name": second.name,
                        "display_name": "保留原编码",
                    },
                )
            )[0],
            200,
        )

    async def test_required_fields_lengths_and_boolean_validation(self):
        for body in [
            {"name": " ", "display_name": "权限"},
            {"name": "api:test:get", "display_name": " "},
            {"name": "a" * 129, "display_name": "权限"},
            {"name": "api:test:get", "display_name": "a" * 257},
            {"name": "api:test:get", "display_name": "权限", "is_editable": None},
        ]:
            with self.subTest(body=body):
                self.assertEqual((await self.request("POST", "/create", body))[0], 422)
        _, created = await self.request(
            "POST",
            "/create",
            {
                "name": "api:default:get",
                "display_name": "默认保护",
            },
        )
        self.assertFalse(created["is_editable"])
        self.assertFalse(created["is_deletable"])

    async def test_missing_records(self):
        self.assertEqual((await self.request("GET", "/999"))[0], 404)
        self.assertEqual(
            (
                await self.request(
                    "PUT",
                    "/999/update",
                    {
                        "name": "api:missing:get",
                        "display_name": "不存在",
                    },
                )
            )[0],
            404,
        )
        self.assertEqual((await self.request("DELETE", "/999/remove"))[0], 404)

    async def test_operation_permissions_are_separate(self):
        record = self.create_record()
        self.set_actor(permissions=["api:permission:POST, put"])
        self.assertEqual(
            (
                await self.request(
                    "POST",
                    "/create",
                    {
                        "name": "api:new:get",
                        "display_name": "新权限",
                    },
                )
            )[0],
            200,
        )
        self.assertEqual(
            (
                await self.request(
                    "PUT",
                    f"/{record.id}/update",
                    {
                        "name": record.name,
                        "display_name": "允许更新",
                    },
                )
            )[0],
            200,
        )
        self.assertEqual((await self.request("DELETE", f"/{record.id}/remove"))[0], 403)
        self.set_actor(permissions=["api:permission:delete", record.name])
        self.assertEqual(
            (
                await self.request(
                    "PUT",
                    f"/{record.id}/update",
                    {
                        "name": record.name,
                        "display_name": "禁止更新",
                    },
                )
            )[0],
            403,
        )
        self.assertEqual((await self.request("DELETE", f"/{record.id}/remove"))[0], 200)

    async def test_unrelated_and_inactive_roles_cannot_manage_permissions(self):
        for role_name, grants in [
            ("member", []),
            ("role_admin", []),
            ("member", ["api:permission:postscript"]),
        ]:
            self.set_actor(role_name, permissions=grants)
            self.assertEqual(
                (
                    await self.request(
                        "POST",
                        "/create",
                        {
                            "name": "api:forbidden:get",
                            "display_name": "无授权",
                        },
                    )
                )[0],
                403,
            )
        self.set_actor("admin", active=False)
        self.assertEqual(
            (
                await self.request(
                    "POST",
                    "/create",
                    {
                        "name": "api:inactive:get",
                        "display_name": "停用用户",
                    },
                )
            )[0],
            403,
        )
        self.set_actor("permission_admin")
        self.actor.roles[0].is_active = False
        self.assertEqual(
            (
                await self.request(
                    "POST",
                    "/create",
                    {
                        "name": "api:inactive-role:get",
                        "display_name": "停用角色",
                    },
                )
            )[0],
            403,
        )
        self.set_actor("permission_admin")
        self.assertEqual(
            (
                await self.request(
                    "POST",
                    "/create",
                    {
                        "name": "api:authorized:get",
                        "display_name": "已授权",
                    },
                )
            )[0],
            200,
        )

    async def test_in_use_permission_requires_unlinking_before_deletion(self):
        record = self.create_record()
        other = self.create_record("api:other:get")
        role = Role(
            name="assigned", display_name="关联角色", permissions=[record, other],
            is_editable=True, is_active=False,
        )
        self.session.add(role)
        self.session.commit()
        code, body = await self.request("DELETE", f"/{record.id}/remove")
        self.assertEqual(code, 409)
        self.assertEqual(body["detail"]["code"], "permission_in_use")
        self.assertEqual(body["detail"]["role_count"], 1)
        self.assertIsNotNone(self.session.get(Permission, record.id))
        self.assertEqual(len(self.session.execute(select(role_permissions)).all()), 2)
        self.assertEqual(self.session.execute(select(AuthorizationAudit)).all(), [])
        for path in ["/list", "/page", f"/{record.id}"]:
            code, result = await self.request("GET", path)
            items = result if isinstance(result, list) else result.get("items", [result])
            self.assertEqual(next(item["role_count"] for item in items if item["id"] == record.id), 1)
        code, references = await self.request("GET", f"/{record.id}/roles")
        self.assertEqual((code, references["total"]), (200, 1))
        self.assertEqual(references["items"][0]["name"], "assigned")
        self.assertEqual((await self.request("PUT", f"/{role.id}/permissions",
            {"permission_ids": [other.id]}, resource="role"))[0], 200)
        self.assertEqual((await self.request("GET", f"/{record.id}"))[1]["role_count"], 0)
        self.assertEqual((await self.request("DELETE", f"/{record.id}/remove"))[0], 200)
        self.assertIsNotNone(self.session.get(Role, role.id))
        assignments = self.session.execute(select(role_permissions)).all()
        self.assertEqual(
            [(item.role_id, item.permission_id) for item in assignments],
            [(role.id, other.id)],
        )
        audit = self.session.scalar(select(AuthorizationAudit).where(
            AuthorizationAudit.target_type == "permission", AuthorizationAudit.action == "delete"))
        self.assertEqual(audit.before["name"], record.name)
        self.assertEqual(audit.before["display_name"], record.display_name)
        self.assertEqual(audit.before["role_ids"], [])

    async def test_permission_references_are_paginated_and_require_role_access(self):
        record = self.create_record()
        self.session.add_all([Role(name=f"ref_{i}", display_name=f"角色 {i}", permissions=[record]) for i in range(3)])
        self.session.commit()
        code, result = await self.request("GET", f"/{record.id}/roles", query="page=2&page_size=2")
        self.assertEqual((code, result["total"], len(result["items"])), (200, 3, 1))
        self.assertEqual(result["items"][0]["name"], "ref_2")
        self.set_actor(permissions=["api:permission:read"])
        self.assertEqual((await self.request("GET", f"/{record.id}/roles"))[0], 403)
        for access in ["api:role:read", "api:role:assign-permission"]:
            self.set_actor(permissions=["api:permission:read", access])
            self.assertEqual((await self.request("GET", f"/{record.id}/roles"))[0], 200)
        self.set_actor("admin")
        self.assertEqual((await self.request("GET", "/999/roles"))[0], 404)
        self.assertEqual((await self.request("GET", f"/{record.id}/roles", query="page=0"))[0], 422)

    async def test_initialized_permission_admin_can_manage_permissions_and_references(self):
        # 执行初始化文件中的真实授权语句，避免测试角色与默认配置脱节。
        source = (Path(__file__).resolve().parents[1] / "sql" / "init.sql").read_text()
        catalog = re.findall(
            r"INSERT INTO public\.permissions [^;]*VALUES \('([^']+)', '([^']+)'",
            source,
        )
        self.session.add_all([
            Permission(name=name, display_name=display_name)
            for name, display_name in catalog
        ])
        role = Role(name="permission_admin", display_name="权限管理员", is_active=True)
        self.session.add(role)
        self.session.commit()
        grants = re.findall(
            r"INSERT INTO public\.role_permissions [^;]*r\.name = 'permission_admin'[^;]*;",
            source,
        )
        self.assertTrue(grants)
        # 重复执行验证初始化授权可幂等补齐现有角色。
        for _ in range(2):
            for statement in grants:
                self.session.execute(text(statement.replace("public.", "")))
        self.session.commit()
        self.session.expire(role, ["permissions"])
        self.set_actor("permission_admin", permissions=[p.name for p in role.permissions])

        code, created = await self.request("POST", "/create", {
            "name": "api:permission:get,put", "display_name": "测试权限",
            "is_editable": True, "is_deletable": True,
        })
        self.assertEqual(code, 200)
        permission_id = created["id"]
        self.assertEqual((await self.request("GET", f"/{permission_id}"))[0], 200)
        self.assertEqual((await self.request("PUT", f"/{permission_id}/update", {
            "name": created["name"], "display_name": "已更新",
        }))[0], 200)

        referenced_role = Role(
            name="reference", display_name="引用角色", is_editable=True,
            permissions=[self.session.get(Permission, permission_id)],
        )
        self.session.add(referenced_role)
        self.session.commit()
        code, references = await self.request("GET", f"/{permission_id}/roles")
        self.assertEqual((code, references["total"]), (200, 1))
        code, options = await self.request(
            "GET", f"/{referenced_role.id}/permission-options", resource="role",
        )
        self.assertEqual(code, 200)
        self.assertTrue(options["editable"])
        self.assertEqual((await self.request("PUT", f"/{referenced_role.id}/permissions", {
            "permission_ids": [],
        }, resource="role"))[0], 200)
        self.assertEqual((await self.request("DELETE", f"/{permission_id}/remove"))[0], 200)

        # 补齐权限管理能力后，跨模块维护及全局授权仍遵循现有授权范围。
        global_permission = self.session.scalar(select(Permission).where(Permission.name == "api:*"))
        self.assertEqual((await self.request("PUT", f"/{referenced_role.id}/permissions", {
            "permission_ids": [global_permission.id],
        }, resource="role"))[0], 403)
        self.assertEqual((await self.request("POST", "/create", {
            "name": "forbidden", "display_name": "无授权",
        }, resource="role"))[0], 403)

    async def test_permission_delete_conflict_rolls_back_audit(self):
        record = self.create_record()
        with patch.object(self.db, "commit", AsyncMock(side_effect=IntegrityError("DELETE", {}, Exception("reference")))):
            self.assertEqual((await self.request("DELETE", f"/{record.id}/remove"))[0], 409)
        self.assertIsNotNone(self.session.get(Permission, record.id))
        self.assertEqual(self.session.execute(select(AuthorizationAudit)).all(), [])
        self.assertEqual((await self.request("DELETE", f"/{record.id}/remove"))[0], 200)

    async def test_integrity_conflicts_roll_back_and_allow_retry(self):
        record = self.create_record()
        conflict = IntegrityError("INSERT", {}, Exception("duplicate code"))
        with patch.object(self.db, "commit", AsyncMock(side_effect=conflict)):
            self.assertEqual(
                (
                    await self.request(
                        "POST",
                        "/create",
                        {
                            "name": "api:retry:get",
                            "display_name": "并发创建",
                        },
                    )
                )[0],
                400,
            )
        self.assertEqual(
            (
                await self.request(
                    "POST",
                    "/create",
                    {
                        "name": "api:retry:get",
                        "display_name": "重试创建",
                    },
                )
            )[0],
            200,
        )
        record_id = record.id
        with patch.object(self.db, "commit", AsyncMock(side_effect=conflict)):
            self.assertEqual(
                (
                    await self.request(
                        "PUT",
                        f"/{record_id}/update",
                        {
                            "name": "api:test:get",
                            "display_name": "并发更新",
                        },
                    )
                )[0],
                400,
            )
        _, detail = await self.request("GET", f"/{record_id}")
        self.assertEqual(detail["name"], "api:test:get")
        self.assertEqual(
            (
                await self.request(
                    "PUT",
                    f"/{record_id}/update",
                    {
                        "name": "api:test:get",
                        "display_name": "重试更新",
                    },
                )
            )[0],
            200,
        )

    async def test_anonymous_requests_require_login(self):
        del self.app.dependency_overrides[get_current_user]
        for method, path, body in [
            ("GET", "/list", None),
            ("GET", "/page", None),
            ("GET", "/1", None),
            ("POST", "/create", {"name": "api:test:get", "display_name": "权限"}),
            ("PUT", "/1/update", {"name": "api:test:get", "display_name": "权限"}),
            ("DELETE", "/1/remove", None),
        ]:
            self.assertEqual((await self.request(method, path, body))[0], 401)


if __name__ == "__main__":
    unittest.main()
