"""授权边界回归：通过真实依赖及内存事务检查拒绝、选项与审计。"""

from pathlib import Path
import re
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import test_role_crud as role_tests
import test_user_crud as user_tests
import test_permission as permission_tests
from console_server.api.v1.endpoints.self import read_users_me
from console_server.core.permissions import can_grant, effective_capabilities, has_permission
from console_server.model.rbac import AuthorizationAudit, Permission, Role, User, role_permissions
from console_server.utils.auth import require_permission


class AuthorizationRulesTests(unittest.IsolatedAsyncioTestCase):
    async def test_initialized_user_admin_has_all_user_permissions(self):
        fixture = role_tests.RoleCrudTest()
        fixture.setUp()
        try:
            source = (Path(__file__).resolve().parents[1] / "sql" / "init.sql").read_text()
            catalog = re.findall(
                r"INSERT INTO public\.permissions [^;]*VALUES \('([^']+)', '([^']+)'",
                source,
            )
            permissions = [Permission(name=name, display_name=label) for name, label in catalog]
            fixture.session.add_all(permissions)
            role = Role(name="user_admin", display_name="用户管理员", is_active=True,
                        permissions=[next(p for p in permissions if p.name == "menu:chart")])
            fixture.session.add(role)
            fixture.session.commit()
            grants = re.findall(
                r"INSERT INTO public\.role_permissions [^;]*r\.name = 'user_admin'[^;]*;",
                source,
            )
            self.assertTrue(grants)
            for _ in range(2):
                for statement in grants:
                    fixture.session.execute(text(statement.replace("public.", "")))
            fixture.session.commit()
            fixture.session.expire(role, ["permissions"])
            names = {p.name for p in role.permissions}
            expected = {name for name, _ in catalog if name.startswith("api:user:")}
            expected.update(("menu:user", "menu:dashboard", "menu:setting", "menu:chart"))
            self.assertEqual(names, expected)
            count = fixture.session.scalar(select(func.count()).select_from(role_permissions)
                                           .where(role_permissions.c.role_id == role.id))
            self.assertEqual(count, len(expected))
            user = User(is_active=True, roles=[role])
            for action in ("read", "create", "update", "delete", "assign-role"):
                await require_permission("user", f"api:user:{action}")(user)
            self.assertIn("menu:user", effective_capabilities(names))
            for module in ("role", "permission"):
                with self.assertRaises(HTTPException):
                    await require_permission(module, f"api:{module}:read")(user)
        finally:
            fixture.tearDown()

    async def test_role_names_never_authorize(self):
        for name in ("admin", "user_admin", "role_admin", "permission_admin"):
            user = User(is_active=True, roles=[Role(name=name, is_active=True, permissions=[])])
            for module in ("user", "role", "permission"):
                with self.assertRaises(HTTPException):
                    await require_permission(module, f"api:{module}:put")(user)

    async def test_maintenance_does_not_include_grants(self):
        for module, action in (("user", "assign-role"), ("role", "assign-permission")):
            for names in ([f"api:{module}:*"], [f"api:{module}:GET,POST,PUT,DELETE"]):
                self.assertTrue(has_permission(names, f"api:{module}:update"))
                self.assertFalse(has_permission(names, f"api:{module}:{action}"))
            self.assertTrue(has_permission(["api:*"], f"api:{module}:{action}"))

    async def test_disabled_roles_are_absent_from_current_user(self):
        user = User(id=1, name="test", email="test@example.com", is_active=True,
                    is_editable=True, is_deletable=True, roles=[
            Role(id=1, name="disabled", display_name="停用", is_active=False,
                 permissions=[Permission(id=1, name="api:*", display_name="全局")]),
            Role(id=2, name="reader", display_name="只读", is_active=True,
                 permissions=[Permission(id=2, name="api:user:get", display_name="查询用户")]),
        ])
        current = await read_users_me(user)
        self.assertEqual([role.name for role in current.roles], ["reader"])
        self.assertEqual([p.name for p in current.permissions], ["api:user:get"])
        self.assertIn("api:user:read", current.capabilities)
        self.assertIn("menu:user", current.capabilities)
        self.assertNotIn("api:user:assign-role", current.capabilities)

    async def test_grant_scope_respects_wildcards_and_methods(self):
        self.assertFalse(can_grant(["api:user:*", "api:user:assign-role"], "api:*"))
        self.assertFalse(can_grant(["api:user:*"], "api:role:read"))
        self.assertFalse(can_grant(["api:user:read"], "api:user:get,put"))
        self.assertTrue(can_grant(["api:user:*"], "api:user:get,put"))
        self.assertTrue(can_grant(["api:*"], "menu:chart"))
        self.assertFalse(has_permission(["api:user:output"], "api:user:put"))

    async def test_menu_wildcard_does_not_grant_management_access(self):
        capabilities = effective_capabilities(["menu:*", "api:role:get"])
        self.assertIn("menu:role", capabilities)
        self.assertNotIn("menu:user", capabilities)
        self.assertNotIn("menu:permission", capabilities)
        self.assertNotIn("menu:*", capabilities)


class RoleGrantBoundaryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.fixture = role_tests.RoleCrudTest()
        self.fixture.setUp()
        self.fixture.seed_permissions()
        global_permission = Permission(id=4, name="api:*", display_name="全局管理")
        self.fixture.session.add(global_permission)
        self.fixture.session.commit()

    def tearDown(self):
        self.fixture.tearDown()

    def actor(self, *names):
        self.fixture.current_user = self.fixture.make_user("operator", names)

    async def test_editor_cannot_assign_permissions_through_either_endpoint(self):
        self.actor("api:role:put", "api:role:post", "api:user:*")
        for method, suffix in (("PUT", "permissions"), ("POST", "assign-permissions")):
            code, _ = await self.fixture.request(method, f"/2/{suffix}", {"permission_ids": [4]})
            self.assertEqual(code, 403)
        self.assertEqual([p.id for p in self.fixture.session.get(Role, 2).permissions], [1, 3])

    async def test_grant_operator_cannot_escalate_or_clear_other_scopes(self):
        self.actor("api:role:assign-permission", "api:role:get", "api:user:get", "api:user:put")
        for method, suffix in (("PUT", "permissions"), ("POST", "assign-permissions")):
            code, _ = await self.fixture.request(method, f"/2/{suffix}", {"permission_ids": [4]})
            self.assertEqual(code, 403)
        self.actor("api:role:assign-permission", "api:user:get")
        code, _ = await self.fixture.request("PUT", "/2/permissions", {"permission_ids": []})
        self.assertEqual(code, 403)
        self.assertEqual(self.fixture.session.scalar(select(func.count()).select_from(AuthorizationAudit)), 0)

    async def test_options_and_grants_share_scope_and_audit(self):
        self.actor("api:role:assign-permission", "api:role:get", "api:user:get", "api:user:put")
        code, options = await self.fixture.request("GET", "/2/permission-options")
        self.assertEqual(code, 200)
        self.assertTrue(options["editable"])
        self.assertEqual(options["grantable_permission_ids"], [1, 2, 3])
        self.assertNotIn(4, [p["id"] for p in options["permissions"]])
        for _ in range(2):
            code, _ = await self.fixture.request("PUT", "/2/permissions", {"permission_ids": [1, 2, 3]})
            self.assertEqual(code, 200)
        audits = self.fixture.session.scalars(select(AuthorizationAudit)).all()
        self.assertEqual(len(audits), 1)
        self.assertEqual((audits[0].before, audits[0].after), ([1, 3], [1, 2, 3]))

    async def test_failed_grant_rolls_back_audit_and_associations(self):
        conflict = IntegrityError("INSERT", {}, Exception("conflict"))
        with patch.object(self.fixture.database, "commit", AsyncMock(side_effect=conflict)):
            code, _ = await self.fixture.request("PUT", "/2/permissions", {"permission_ids": [2]})
            self.assertEqual(code, 400)
        self.assertEqual(self.fixture.session.scalar(select(func.count()).select_from(AuthorizationAudit)), 0)
        self.assertEqual([p.id for p in self.fixture.session.get(Role, 2).permissions], [1, 3])

    async def test_cannot_reactivate_role_outside_scope(self):
        role = self.fixture.session.get(Role, 2)
        role.permissions = [self.fixture.session.get(Permission, 4)]
        role.is_active = False
        self.fixture.session.commit()
        self.actor("api:role:put")
        code, _ = await self.fixture.request("PUT", "/2/update", {"display_name": "禁止启用", "is_active": True})
        self.assertEqual(code, 403)
        self.assertFalse(role.is_active)


class UserGrantBoundaryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.fixture = user_tests.UserCrudTests()
        self.fixture.setUp()
        with Session(self.fixture.engine) as session:
            role = session.get(Role, 4)
            role.permissions.append(Permission(name="api:user:assign-role", display_name="分配角色"))
            session.commit()

    def tearDown(self):
        self.fixture.tearDown()

    async def test_delegation_rejects_admin_and_all_legacy_routes_use_same_scope(self):
        for method, suffix in (("PUT", "roles"), ("POST", "assign-roles")):
            code, _ = await self.fixture.request(method, f"/api/v1/user/2/{suffix}", {"role_ids": [2, 1]}, actor=4)
            self.assertEqual(code, 403)
        code, options = await self.fixture.request("GET", "/api/v1/user/2/role-options", actor=4)
        self.assertEqual(code, 200)
        self.assertNotIn(1, options["grantable_role_ids"])
        for method, suffix, ids in (("PUT", "roles", [2, 3]), ("POST", "remove-roles", [3])):
            code, _ = await self.fixture.request(method, f"/api/v1/user/2/{suffix}", {"role_ids": ids}, actor=4)
            self.assertEqual(code, 200)
        with Session(self.fixture.engine) as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(AuthorizationAudit)), 2)

    async def test_editor_without_assignment_permission_is_rejected(self):
        with Session(self.fixture.engine) as session:
            role = session.get(Role, 4)
            role.permissions = [p for p in role.permissions if p.name != "api:user:assign-role"]
            session.commit()
        for method, suffix in (("PUT", "roles"), ("POST", "assign-roles"), ("POST", "remove-roles")):
            code, _ = await self.fixture.request(method, f"/api/v1/user/2/{suffix}", {"role_ids": [3]}, actor=4)
            self.assertEqual(code, 403)


class PermissionCatalogBoundaryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.fixture = permission_tests.PermissionApiTestCase()
        self.fixture.setUp()
        self.record = self.fixture.create_record()

    def tearDown(self):
        self.fixture.tearDown()

    async def test_read_requires_permission_and_code_is_immutable(self):
        self.fixture.set_actor("member")
        for path in ("/list", "/page", f"/{self.record.id}"):
            self.assertEqual((await self.fixture.request("GET", path))[0], 403)
        self.fixture.set_actor("admin")
        for code in ("api:*", "api:test:post"):
            self.assertEqual((await self.fixture.request("PUT", f"/{self.record.id}/update",
                {"name": code, "display_name": "禁止变更编码"}))[0], 400)
        self.assertEqual(self.record.name, "api:test:get")

    async def test_invalid_permission_codes_are_rejected(self):
        for code in ("user_update", "api::get", "api:user:", "api:user:get:extra", "menu:"):
            self.assertEqual((await self.fixture.request("POST", "/create",
                {"name": code, "display_name": "非法编码"}))[0], 422)
