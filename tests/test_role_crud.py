"""角色 CRUD 的接口回归测试，使用内存数据库，不修改业务数据。"""

import json
from pathlib import Path
import re
import unittest
from unittest.mock import AsyncMock, patch
from urllib.parse import urlencode

from fastapi import FastAPI, HTTPException
from sqlalchemy import create_engine, event, func, insert, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from console_server.api.v1.endpoints.role import router
from console_server.api.v1.endpoints.user import router as user_router
from console_server.db.database import get_db
from console_server.core.permissions import effective_capabilities
from console_server.model.common import Base
from console_server.model.rbac import AuthorizationAudit, Permission, Role, User, role_permissions, user_roles
from console_server.utils.auth import get_current_user, require_permission


class DatabaseAdapter:
    """将同步内存会话适配为接口使用的异步方法，执行真实 SQL 和事务。"""

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


class RoleCrudTest(unittest.IsolatedAsyncioTestCase):
    """通过 ASGI 请求验证路由、参数校验、权限依赖和数据库写入。"""

    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")

        @event.listens_for(self.engine, "connect")
        def enable_foreign_keys(connection, record):
            connection.execute("PRAGMA foreign_keys=ON")

        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine, expire_on_commit=False)
        self.database = DatabaseAdapter(self.session)
        self.session.add_all([
            Role(id=1, name="admin", display_name="管理员", is_active=True),
            Role(
                id=2, name="custom", display_name="自定义角色", description="原描述",
                is_active=True, is_editable=True, is_deletable=True,
            ),
        ])
        self.session.commit()
        self.current_user = self.make_user("admin")
        self.app = FastAPI()
        self.app.include_router(router, prefix="/api/v1")
        self.app.include_router(user_router, prefix="/api/v1")

        async def override_db():
            yield self.database

        # 保留真实权限依赖，仅替换 JWT 登录身份和数据库会话。
        self.app.dependency_overrides[get_db] = override_db
        self.app.dependency_overrides[get_current_user] = lambda: self.current_user

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    @staticmethod
    def make_user(role_name, permission_names=(), role_active=True, user_active=True):
        """构造带指定角色、接口权限和启用状态的登录用户。"""
        if not permission_names and role_name == "admin":
            permission_names = ("api:*",)
        elif not permission_names and role_name.endswith("_admin"):
            permission_names = (f"api:{role_name[:-6]}:*",)
        role = Role(
            name=role_name, is_active=role_active,
            permissions=[Permission(name=name) for name in permission_names],
        )
        return User(is_active=user_active, roles=[role])

    async def request(self, method, path, body=None, query="", resource="role"):
        """直接发送 ASGI 请求，保留 FastAPI 的输入验证和响应序列化。"""
        messages = []
        request_body = json.dumps(body).encode() if body is not None else b""

        async def receive():
            return {"type": "http.request", "body": request_body, "more_body": False}

        async def send(message):
            messages.append(message)

        await self.app({
            "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
            "method": method, "scheme": "http", "path": f"/api/v1/{resource}{path}",
            "raw_path": f"/api/v1/{resource}{path}".encode(), "query_string": query.encode(),
            "root_path": "", "headers": [(b"content-type", b"application/json")],
            "client": ("127.0.0.1", 12345), "server": ("test", 80),
        }, receive, send)
        status_code = next(
            message["status"] for message in messages
            if message["type"] == "http.response.start"
        )
        response_body = b"".join(
            message.get("body", b"") for message in messages
            if message["type"] == "http.response.body"
        )
        return status_code, json.loads(response_body)

    async def test_search_combines_text_status_flags_and_pagination(self):
        self.session.add_all([
            Role(name="audit_one", display_name="Team Auditor", is_active=False, is_editable=True, is_deletable=False),
            Role(name="audit_two", display_name="Team Auditor", is_active=False, is_editable=True, is_deletable=False),
            Role(name="audit_active", display_name="Team Auditor", is_active=True, is_editable=True, is_deletable=False),
        ])
        self.session.commit()
        query = urlencode({"name": " AUDIT ", "display_name": "team", "is_active": "false", "is_editable": "true", "is_deletable": "false", "page": 2, "page_size": 1})
        code, result = await self.request("GET", "/list", query=query)
        self.assertEqual((code, result["total"], result["total_pages"]), (200, 2, 2))
        self.assertEqual([item["name"] for item in result["items"]], ["audit_two"])
        _, empty = await self.request("GET", "/list", query="is_active=false&is_deletable=true")
        self.assertEqual((empty["items"], empty["total"]), ([], 0))
        _, all_roles = await self.request("GET", "/list", query="name=%20%20&display_name=")
        self.assertEqual(all_roles["total"], 5)

    async def test_search_keyword_literal_wildcards_and_validation(self):
        self.session.add(Role(name="literal_%", display_name="Literal", description="Unique needle"))
        self.session.commit()
        for query in [{"name": "_%"}, {"keyword": " NEEDLE "}, {"keyword": "literal"}]:
            code, result = await self.request("GET", "/list", query=urlencode(query))
            self.assertEqual((code, result["total"]), (200, 1))
            self.assertEqual(result["items"][0]["name"], "literal_%")
        for query in ["is_active=invalid", "is_editable=invalid", "is_deletable=invalid", "display_name=" + "a" * 129]:
            self.assertEqual((await self.request("GET", "/list", query=query))[0], 422)

    async def test_create_read_update_delete(self):
        """验证新增、详情、更新状态及删除的完整流程。"""
        status_code, body = await self.request("POST", "/create", {
            "name": "  auditor  ", "display_name": "  审计员  ",
        })
        self.assertEqual((status_code, body), (200, {"detail": "ok"}))
        role = self.session.scalar(select(Role).where(Role.name == "auditor"))
        self.assertEqual(role.display_name, "审计员")
        self.assertTrue(role.is_editable and role.is_deletable and role.is_active)

        status_code, body = await self.request("GET", f"/{role.id}/detail")
        self.assertEqual(status_code, 200)
        self.assertEqual(body["name"], "auditor")
        self.assertIsNone(body["description"])
        self.assertTrue(body["is_editable"] and body["is_deletable"])
        self.assertIn("created_at", body)
        self.assertIn("updated_at", body)

        status_code, _ = await self.request("PUT", f"/{role.id}/update", {
            "display_name": "  新审计员  ", "description": "新描述", "is_active": False,
        })
        self.assertEqual(status_code, 200)
        self.assertEqual(role.display_name, "新审计员")
        self.assertEqual(role.description, "新描述")
        self.assertFalse(role.is_active)
        self.assertEqual(role.name, "auditor")

        status_code, _ = await self.request("DELETE", f"/{role.id}/remove")
        self.assertEqual(status_code, 200)
        self.assertIsNone(self.session.scalar(select(Role).where(Role.id == role.id)))

    async def test_duplicate_identifier(self):
        status_code, body = await self.request("POST", "/create", {
            "name": " custom ", "display_name": "重复角色",
        })
        self.assertEqual(status_code, 400)
        self.assertEqual(body["detail"], "Role name already exists")
        self.assertEqual(self.session.scalar(select(func.count()).select_from(Role)), 2)

    async def test_concurrent_duplicate_rolls_back(self):
        """模拟唯一性查询后出现并发写入，验证回滚和业务错误转换。"""
        original_commit = self.database.commit

        async def competing_commit():
            with self.engine.begin() as connection:
                connection.execute(insert(Role).values(name="racing", display_name="并发角色"))
            await original_commit()

        with patch.object(self.database, "commit", competing_commit):
            status_code, body = await self.request("POST", "/create", {
                "name": "racing", "display_name": "本次创建",
            })
        self.assertEqual(status_code, 400)
        self.assertEqual(body["detail"], "Role name already exists")
        self.assertEqual(
            self.session.scalar(select(Role).where(Role.name == "racing")).display_name,
            "并发角色",
        )

    async def test_unrelated_integrity_error_is_not_reported_as_duplicate(self):
        failure = IntegrityError("insert", {}, Exception("other constraint"))
        with patch.object(self.database, "commit", side_effect=failure):
            with self.assertRaises(IntegrityError):
                await self.request("POST", "/create", {"name": "valid", "display_name": "有效"})
        self.assertIsNone(self.session.scalar(select(Role).where(Role.name == "valid")))

    async def test_create_validation(self):
        valid = {"name": "new_role", "display_name": "新角色"}
        invalid = [
            {"name": ""}, {"name": "   "}, {"name": "a" * 65}, {"name": None},
            {"display_name": ""}, {"display_name": "   "},
            {"display_name": "a" * 129}, {"display_name": None},
            {"is_active": None}, {"is_editable": False}, {"is_deletable": False},
        ]
        for fields in invalid:
            with self.subTest(fields=fields):
                status_code, _ = await self.request("POST", "/create", {**valid, **fields})
                self.assertEqual(status_code, 422)
        self.assertEqual(self.session.scalar(select(func.count()).select_from(Role)), 2)

    async def test_update_validation_and_identifier_immutability(self):
        for fields in [
            {"display_name": " "}, {"display_name": "a" * 129}, {"display_name": None},
            {"display_name": "新名称", "is_active": None},
            {"display_name": "新名称", "name": "renamed"},
            {"display_name": "新名称", "is_editable": False},
        ]:
            with self.subTest(fields=fields):
                status_code, _ = await self.request("PUT", "/2/update", fields)
                self.assertEqual(status_code, 422)
        self.assertEqual(self.session.get(Role, 2).name, "custom")
        self.assertEqual(self.session.get(Role, 2).display_name, "自定义角色")

    async def test_clear_description(self):
        status_code, _ = await self.request("PUT", "/2/update", {
            "display_name": "自定义角色", "description": None, "is_active": True,
        })
        self.assertEqual(status_code, 200)
        self.assertIsNone(self.session.get(Role, 2).description)

    async def test_protected_roles(self):
        for editable, deletable in [(False, False), (True, True)]:
            role = self.session.get(Role, 1)
            role.is_editable, role.is_deletable = editable, deletable
            self.session.commit()
            for method, path, body in [
                ("PUT", "/1/update", {"display_name": "禁用管理员", "is_active": False}),
                ("DELETE", "/1/remove", None),
            ]:
                with self.subTest(editable=editable, method=method):
                    status_code, _ = await self.request(method, path, body)
                    self.assertEqual(status_code, 400)
            self.assertTrue(role.is_active)
            self.assertEqual(role.display_name, "管理员")

    async def test_independent_protection_flags(self):
        role = self.session.get(Role, 2)
        role.is_editable, role.is_deletable = False, True
        self.session.commit()
        status_code, _ = await self.request("PUT", "/2/update", {"display_name": "拒绝更新"})
        self.assertEqual(status_code, 400)
        role.is_editable, role.is_deletable = True, False
        self.session.commit()
        status_code, _ = await self.request("DELETE", "/2/remove")
        self.assertEqual(status_code, 400)

    async def test_in_use_role_requires_unlinking_before_deletion(self):
        role = self.session.get(Role, 2)
        permission = Permission(name="api:role:get", display_name="查询角色")
        role.permissions.append(permission)
        user = User(name="关联用户", email="test@example.com", password="test", roles=[role],
                    is_active=False, is_editable=True)
        self.session.add(user)
        self.session.commit()
        self.session.expire_all()
        status_code, body = await self.request("DELETE", "/2/remove")
        self.assertEqual(status_code, 409)
        self.assertEqual(body["detail"]["code"], "role_in_use")
        self.assertEqual(body["detail"]["user_count"], 1)
        self.assertIsNotNone(self.session.get(Role, 2))
        self.assertEqual(self.session.scalar(select(func.count()).select_from(user_roles)), 1)
        self.assertEqual(self.session.scalar(select(func.count()).select_from(role_permissions)), 1)
        self.assertEqual(self.session.execute(select(AuthorizationAudit)).all(), [])
        self.assertEqual((await self.request("GET", "/2/detail"))[1]["user_count"], 1)
        self.assertEqual((await self.request("GET", "/list"))[1]["items"][1]["user_count"], 1)
        code, references = await self.request("GET", "/2/users")
        self.assertEqual((code, references["total"]), (200, 1))
        self.assertNotIn("password", references["items"][0])
        self.assertEqual((await self.request("PUT", f"/{user.id}/roles", {"role_ids": []}, resource="user"))[0], 200)
        self.assertEqual((await self.request("GET", "/2/detail"))[1]["user_count"], 0)
        self.assertEqual((await self.request("DELETE", "/2/remove"))[0], 200)
        self.assertEqual(self.session.scalar(select(func.count()).select_from(user_roles)), 0)
        self.assertEqual(self.session.scalar(select(func.count()).select_from(role_permissions)), 0)
        self.assertIsNotNone(self.session.get(User, user.id))
        self.assertIsNotNone(self.session.get(Permission, permission.id))
        audit = self.session.scalar(select(AuthorizationAudit).where(
            AuthorizationAudit.target_type == "role", AuthorizationAudit.action == "delete"))
        self.assertEqual(audit.before["name"], "custom")
        self.assertEqual(audit.before["display_name"], "自定义角色")
        self.assertEqual(audit.before["permissions"][0]["name"], "api:role:get")

    async def test_role_references_are_paginated_and_require_user_access(self):
        role = self.session.get(Role, 2)
        self.session.add_all([User(name=f"用户 {i}", email=f"ref{i}@example.com", password="secret", roles=[role]) for i in range(3)])
        self.session.commit()
        code, result = await self.request("GET", "/2/users", query="page=2&page_size=2")
        self.assertEqual((code, result["total"], len(result["items"])), (200, 3, 1))
        self.assertEqual(result["items"][0]["email"], "ref2@example.com")
        self.current_user = self.make_user("reader", ["api:role:read"])
        self.assertEqual((await self.request("GET", "/2/users"))[0], 403)
        for access in ["api:user:read", "api:user:assign-role"]:
            self.current_user = self.make_user("reader", ["api:role:read", access])
            self.assertEqual((await self.request("GET", "/2/users"))[0], 200)
        self.current_user = self.make_user("admin")
        self.assertEqual((await self.request("GET", "/999/users"))[0], 404)
        self.assertEqual((await self.request("GET", "/2/users", query="page_size=0"))[0], 422)

    async def test_role_delete_conflict_rolls_back_permissions_and_audit(self):
        role = self.session.get(Role, 2)
        permission = Permission(name="api:role:get", display_name="查询角色")
        role.permissions.append(permission)
        self.session.commit()
        with patch.object(self.database, "commit", AsyncMock(side_effect=IntegrityError("DELETE", {}, Exception("reference")))):
            self.assertEqual((await self.request("DELETE", "/2/remove"))[0], 409)
        self.assertIsNotNone(self.session.get(Role, 2))
        self.assertEqual(self.session.scalar(select(func.count()).select_from(role_permissions)), 1)
        self.assertEqual(self.session.execute(select(AuthorizationAudit)).all(), [])
        self.assertEqual((await self.request("DELETE", "/2/remove"))[0], 200)

    async def test_missing_role_and_invalid_ids(self):
        for method, suffix, body in [
            ("GET", "detail", None), ("PUT", "update", {"display_name": "名称"}),
            ("DELETE", "remove", None),
        ]:
            status_code, response = await self.request(method, f"/999/{suffix}", body)
            self.assertEqual(status_code, 400)
            self.assertEqual(response["detail"], "Role not found")
            for role_id in ["0", "-1", "invalid"]:
                with self.subTest(method=method, role_id=role_id):
                    status_code, _ = await self.request(method, f"/{role_id}/{suffix}", body)
                    self.assertEqual(status_code, 422)

    async def test_list_pagination_and_protection_fields(self):
        for index in reversed(range(3, 24)):
            self.session.add(Role(name=f"role_{index}", display_name=f"角色 {index}", id=index))
        self.session.commit()
        status_code, body = await self.request("GET", "/list", query="page=3&page_size=10")
        self.assertEqual(status_code, 200)
        self.assertEqual([role["id"] for role in body["items"]], [21, 22, 23])
        self.assertEqual((body["total"], body["total_pages"]), (23, 3))
        self.assertFalse(body["items"][0]["is_editable"])
        status_code, body = await self.request("GET", "/list", query="page=4&page_size=10")
        self.assertEqual(status_code, 200)
        self.assertEqual(body["items"], [])
        for query in ["page=0", "page_size=0", "page_size=101"]:
            status_code, _ = await self.request("GET", "/list", query=query)
            self.assertEqual(status_code, 422)

    async def test_ordinary_user_cannot_access_crud(self):
        self.current_user = self.make_user("ordinary")
        for method, path, body in [
            ("GET", "/list", None), ("GET", "/2/detail", None),
            ("POST", "/create", {"name": "new", "display_name": "新角色"}),
            ("PUT", "/2/update", {"display_name": "新名称"}), ("DELETE", "/2/remove", None),
        ]:
            with self.subTest(method=method, path=path):
                status_code, _ = await self.request(method, path, body)
                self.assertEqual(status_code, 403)

    async def test_anonymous_requests_require_login(self):
        """移除测试身份后，无登录凭证的请求必须在执行业务前被拒绝。"""
        self.app.dependency_overrides.pop(get_current_user)
        for method, path, body in [
            ("GET", "/list", None), ("GET", "/2/detail", None),
            ("POST", "/create", {"name": "new", "display_name": "新角色"}),
            ("PUT", "/2/update", {"display_name": "新名称"}), ("DELETE", "/2/remove", None),
        ]:
            with self.subTest(method=method, path=path):
                status_code, _ = await self.request(method, path, body)
                self.assertEqual(status_code, 401)

    async def test_update_uses_role_permission(self):
        self.current_user = self.make_user("operator", ["api:role:put"])
        status_code, _ = await self.request("PUT", "/2/update", {"display_name": "允许更新"})
        self.assertEqual(status_code, 200)
        self.current_user = self.make_user("operator", ["api:permission:put"])
        status_code, _ = await self.request("PUT", "/2/update", {"display_name": "禁止更新"})
        self.assertEqual(status_code, 403)

    async def test_initialized_role_admin_can_manage_roles_permissions_and_linked_users(self):
        source = (Path(__file__).resolve().parents[1] / "sql" / "init.sql").read_text()
        catalog = re.findall(
            r"INSERT INTO public\.permissions [^;]*VALUES \('([^']+)', '([^']+)'",
            source,
        )
        self.session.add_all([
            Permission(name=name, display_name=display_name)
            for name, display_name in catalog
        ])
        operator_role = Role(name="role_admin", display_name="角色管理员", is_active=True)
        self.session.add(operator_role)
        self.session.commit()
        grants = re.findall(
            r"INSERT INTO public\.role_permissions [^;]*r\.name = 'role_admin'[^;]*;",
            source,
        )
        self.assertTrue(grants)
        # 使用真实初始化 SQL，并验证重复补齐不会生成重复关联。
        for _ in range(2):
            for statement in grants:
                self.session.execute(text(statement.replace("public.", "")))
        self.session.commit()
        self.session.expire(operator_role, ["permissions"])
        self.current_user = User(
            name="角色管理员", email="role-admin@example.com", password="test",
            is_active=True, roles=[operator_role],
        )
        self.session.add(self.current_user)
        self.session.commit()
        names = {permission.name for permission in operator_role.permissions}
        self.assertEqual(names, {
            "api:role:*", "api:role:read", "api:role:create", "api:role:update",
            "api:role:delete", "api:role:assign-permission", "api:user:assign-role",
            "menu:role", "menu:dashboard", "menu:setting",
        })
        self.assertIn("menu:role", effective_capabilities(names))
        self.assertNotIn("menu:user", effective_capabilities(names))
        self.assertNotIn("menu:permission", effective_capabilities(names))

        code, _ = await self.request("POST", "/create", {
            "name": "managed_role", "display_name": "待管理角色",
        })
        self.assertEqual(code, 200)
        managed_role = self.session.scalar(select(Role).where(Role.name == "managed_role"))
        role_id = managed_role.id
        self.assertEqual((await self.request("GET", "/list"))[0], 200)
        self.assertEqual((await self.request("GET", f"/{role_id}/detail"))[0], 200)
        self.assertEqual((await self.request("PUT", f"/{role_id}/update", {
            "display_name": "已更新角色",
        }))[0], 200)

        permission = self.session.scalar(select(Permission).where(Permission.name == "api:role:read"))
        code, options = await self.request("GET", f"/{role_id}/permission-options")
        self.assertEqual(code, 200)
        self.assertTrue(options["editable"])
        self.assertIn(permission.id, options["grantable_permission_ids"])
        self.assertEqual((await self.request("POST", f"/{role_id}/assign-permissions", {
            "permission_ids": [permission.id],
        }))[0], 200)
        self.assertEqual((await self.request("GET", f"/{role_id}/permissions"))[1]["permission_ids"], [permission.id])
        self.assertEqual((await self.request("PUT", f"/{role_id}/permissions", {
            "permission_ids": [permission.id],
        }))[0], 200)

        linked_user = User(
            name="关联用户", email="role-linked@example.com", password="test",
            is_active=True, is_editable=True, is_deletable=True, roles=[managed_role],
        )
        self.session.add(linked_user)
        self.session.commit()
        code, users = await self.request("GET", f"/{role_id}/users")
        self.assertEqual((code, users["total"]), (200, 1))
        self.assertEqual(users["items"][0]["id"], linked_user.id)
        code, options = await self.request("GET", f"/{linked_user.id}/role-options", resource="user")
        self.assertEqual(code, 200)
        self.assertIn(role_id, options["grantable_role_ids"])
        self.assertEqual((await self.request("PUT", f"/{linked_user.id}/roles", {
            "role_ids": [],
        }, resource="user"))[0], 200)
        self.assertEqual((await self.request("POST", f"/{linked_user.id}/assign-roles", {
            "role_ids": [role_id],
        }, resource="user"))[0], 200)
        self.assertEqual((await self.request("POST", f"/{linked_user.id}/remove-roles", {
            "role_ids": [role_id],
        }, resource="user"))[0], 200)
        self.assertEqual((await self.request("GET", f"/{role_id}/users"))[1]["total"], 0)

        # 角色相关能力不会授予全局权限或用户资料维护能力。
        for permission_name in ("api:*", "api:user:update", "api:permission:update"):
            outside = self.session.scalar(select(Permission).where(Permission.name == permission_name))
            self.assertEqual((await self.request("PUT", f"/{role_id}/permissions", {
                "permission_ids": [outside.id],
            }))[0], 403)
        self.assertEqual((await self.request("GET", "/list", resource="user"))[0], 403)
        self.assertEqual((await self.request("PUT", f"/{linked_user.id}/update", {
            "name": "无权修改",
        }, resource="user"))[0], 403)
        self.assertEqual((await self.request("PUT", f"/{role_id}/permissions", {
            "permission_ids": [],
        }))[0], 200)
        self.assertEqual((await self.request("DELETE", f"/{role_id}/remove"))[0], 200)
        self.assertIsNotNone(self.session.get(User, linked_user.id))

    def seed_permissions(self):
        """创建权限及初始关联，系统权限的保护标记不应限制角色关联。"""
        permissions = [
            Permission(id=1, name="api:user:get", display_name="查询用户"),
            Permission(id=2, name="api:user:put", display_name="编辑用户"),
            Permission(id=3, name="api:role:get", display_name="查询角色"),
        ]
        self.session.add_all(permissions)
        role = self.session.get(Role, 2)
        role.permissions = [permissions[2], permissions[0]]
        self.session.commit()
        return permissions

    async def test_role_permission_replace_and_clear(self):
        """替换及清空关联时，保留权限实体、角色资料和已分配用户。"""
        permissions = self.seed_permissions()
        role = self.session.get(Role, 2)
        user = User(name="关联用户", email="linked@example.com", password="test", roles=[role])
        self.session.add(user)
        self.session.commit()
        status_code, body = await self.request("GET", "/2/permissions")
        self.assertEqual((status_code, body), (200, {"role_id": 2, "permission_ids": [1, 3]}))

        for selected, expected in [([2, 3, 2], [2, 3]), ([2, 3], [2, 3]), ([], [])]:
            with self.subTest(selected=selected):
                status_code, body = await self.request("PUT", "/2/permissions", {
                    "permission_ids": selected,
                })
                self.assertEqual((status_code, body), (200, {"detail": "ok"}))
                # 从数据库重新读取，验证真实关联而不是会话中的暂存值。
                self.session.expire_all()
                status_code, body = await self.request("GET", "/2/permissions")
                self.assertEqual(body["permission_ids"], expected)
                self.assertEqual(self.session.scalar(select(func.count()).select_from(Permission)), 3)
                self.assertEqual(self.session.scalar(select(func.count()).select_from(user_roles)), 1)
                self.assertEqual(role.name, "custom")
                self.assertEqual(role.display_name, "自定义角色")
                self.assertIsNotNone(self.session.get(User, user.id))
                for permission in permissions:
                    self.assertIsNotNone(self.session.get(Permission, permission.id))

    async def test_role_permission_append_is_idempotent(self):
        """原追加接口仍保留已有权限，空列表和重复提交不会删除或重复关联。"""
        self.seed_permissions()
        for selected in [[2, 2, 1], [2], []]:
            status_code, body = await self.request("POST", "/2/assign-permissions", {
                "permission_ids": selected,
            })
            self.assertEqual((status_code, body), (200, {"detail": "ok"}))
            self.session.expire_all()
            _, body = await self.request("GET", "/2/permissions")
            self.assertEqual(body["permission_ids"], [1, 2, 3])

    async def test_role_permission_missing_records_preserve_associations(self):
        """任何不存在的权限都必须拒绝整次变更，不能留下部分新增或删除。"""
        self.seed_permissions()
        for method, suffix in [("PUT", "permissions"), ("POST", "assign-permissions")]:
            with self.subTest(method=method):
                status_code, body = await self.request(method, f"/999/{suffix}", {
                    "permission_ids": [2],
                })
                self.assertEqual((status_code, body["detail"]), (400, "Role not found"))
                status_code, body = await self.request(method, f"/2/{suffix}", {
                    "permission_ids": [2, 999],
                })
                self.assertEqual((status_code, body["detail"]), (400, "Permission not found"))
                _, body = await self.request("GET", "/2/permissions")
                self.assertEqual(body["permission_ids"], [1, 3])
        status_code, body = await self.request("GET", "/999/permissions")
        self.assertEqual((status_code, body["detail"]), (400, "Role not found"))

    async def test_role_permission_protection_applies_to_all_writes(self):
        """管理员及不可编辑角色均不能通过替换或旧追加接口绕过保护。"""
        self.seed_permissions()
        self.session.get(Role, 1).is_editable = True
        self.session.get(Role, 2).is_editable = False
        self.session.commit()
        for role_id in [1, 2]:
            for method, suffix in [("PUT", "permissions"), ("POST", "assign-permissions")]:
                with self.subTest(role_id=role_id, method=method):
                    status_code, body = await self.request(method, f"/{role_id}/{suffix}", {
                        "permission_ids": [2],
                    })
                    self.assertEqual(
                        (status_code, body["detail"]), (400, "Role permissions cannot be changed")
                    )
            status_code, _ = await self.request("GET", f"/{role_id}/permissions")
            self.assertEqual(status_code, 200)
        _, body = await self.request("GET", "/2/permissions")
        self.assertEqual(body["permission_ids"], [1, 3])

    async def test_role_permission_request_validation(self):
        self.seed_permissions()
        for method, suffix in [("PUT", "permissions"), ("POST", "assign-permissions")]:
            for body in [
                {}, {"permission_ids": None}, {"permission_ids": "1"},
                {"permission_ids": [0]}, {"permission_ids": [-1]},
                {"permission_ids": [True]}, {"permission_ids": ["1"]},
                {"permission_ids": [1.5]}, {"permission_ids": [1], "is_editable": True},
            ]:
                with self.subTest(method=method, body=body):
                    status_code, _ = await self.request(method, f"/2/{suffix}", body)
                    self.assertEqual(status_code, 422)
        for method, suffix, body in [
            ("GET", "permissions", None), ("PUT", "permissions", {"permission_ids": []}),
            ("POST", "assign-permissions", {"permission_ids": []}),
        ]:
            for role_id in ["0", "-1", "invalid"]:
                with self.subTest(method=method, role_id=role_id):
                    status_code, _ = await self.request(method, f"/{role_id}/{suffix}", body)
                    self.assertEqual(status_code, 422)
        _, body = await self.request("GET", "/2/permissions")
        self.assertEqual(body["permission_ids"], [1, 3])

    async def test_role_permission_write_conflict_rolls_back(self):
        """写入失败后回滚完整关联集合，下一次保存仍可正常进行。"""
        self.seed_permissions()
        failure = IntegrityError("update", {}, Exception("concurrent permission removal"))
        with patch.object(self.database, "commit", side_effect=failure):
            status_code, body = await self.request("PUT", "/2/permissions", {
                "permission_ids": [2],
            })
        self.assertEqual((status_code, body["detail"]), (400, "Role permissions changed, please retry"))
        self.session.expire_all()
        _, body = await self.request("GET", "/2/permissions")
        self.assertEqual(body["permission_ids"], [1, 3])
        status_code, _ = await self.request("PUT", "/2/permissions", {"permission_ids": [2]})
        self.assertEqual(status_code, 200)

    async def test_role_permission_operation_authorization(self):
        """查询、替换及追加使用各自的角色权限，其他资源权限不能替代。"""
        self.seed_permissions()
        operations = [
            ("GET", "/2/permissions", None, "get"),
            ("PUT", "/2/permissions", {"permission_ids": [2]}, "assign-permission"),
            ("POST", "/2/assign-permissions", {"permission_ids": [2]}, "assign-permission"),
        ]
        for permission in ["api:role:get", "api:role:put", "api:role:post", "api:permission:put", "api:role:assign-permission"]:
            # 目标权限属于另一个模块，必须同时拥有该权限才能授予。
            self.current_user = self.make_user("operator", [permission, "api:user:put", "api:user:get", "menu:user"])
            if permission == "api:role:assign-permission":
                self.current_user.roles[0].permissions.append(Permission(name="api:role:get"))
            for method, path, body, required_method in operations:
                with self.subTest(permission=permission, method=method):
                    status_code, _ = await self.request(method, path, body)
                    expected = 200 if (permission == f"api:role:{required_method}" or
                        (permission == "api:role:assign-permission" and required_method == "get")) else 403
                    self.assertEqual(status_code, expected)
        self.current_user = self.make_user("operator", ["api:role:*"], role_active=False)
        for method, path, body, _ in operations:
            status_code, _ = await self.request(method, path, body)
            self.assertEqual(status_code, 403)
        self.app.dependency_overrides.pop(get_current_user)
        for method, path, body, _ in operations:
            status_code, _ = await self.request(method, path, body)
            self.assertEqual(status_code, 401)

    async def test_role_permission_changes_affect_authorization(self):
        """更新后的角色权限用于真实授权检查，移除关联后不能继续访问。"""
        self.seed_permissions()
        role = self.session.get(Role, 2)
        user = User(
            name="授权验证用户", email="authorization@example.com", password="test",
            is_active=True, roles=[role],
        )
        self.session.add(user)
        self.session.commit()
        checker = require_permission("user", "api:user:put")
        with self.assertRaises(HTTPException):
            await checker(user)
        await self.request("PUT", "/2/permissions", {"permission_ids": [2]})
        self.assertIs(await checker(user), user)
        await self.request("PUT", "/2/permissions", {"permission_ids": []})
        with self.assertRaises(HTTPException):
            await checker(user)


class RolePermissionTest(unittest.IsolatedAsyncioTestCase):
    """验证角色权限的通配符、方法列表和启用状态规则。"""

    async def test_permission_matrix(self):
        cases = [
            ("admin", [], True), ("role_admin", [], True), ("user_admin", [], False),
            ("ordinary", [], False), ("operator", ["api:*"], True),
            ("operator", ["api:role:*"], True), ("operator", ["api:role:put"], True),
            ("operator", ["api:role:get, POST, PUT"], True),
            ("operator", ["api:role:get"], False), ("operator", ["api:user:put"], False),
            ("operator", ["api:role:output"], False),
        ]
        checker = require_permission("role", "api:role:put")
        for role_name, permission_names, allowed in cases:
            with self.subTest(role=role_name, permissions=permission_names):
                user = RoleCrudTest.make_user(role_name, permission_names)
                if allowed:
                    self.assertIs(await checker(user), user)
                else:
                    with self.assertRaises(HTTPException) as error:
                        await checker(user)
                    self.assertEqual(error.exception.status_code, 403)

    async def test_disabled_role_and_user(self):
        checker = require_permission("role", "api:role:delete")
        for role_name, role_active, user_active in [
            ("admin", False, True), ("role_admin", False, True),
            ("operator", False, True), ("admin", True, False),
        ]:
            with self.subTest(role=role_name, role_active=role_active, user_active=user_active):
                user = RoleCrudTest.make_user(role_name, ["api:*"], role_active, user_active)
                with self.assertRaises(HTTPException) as error:
                    await checker(user)
                self.assertEqual(error.exception.status_code, 403)


if __name__ == "__main__":
    unittest.main()
