"""PostgreSQL 行锁及外键回归；仅在随机独立 schema 中创建测试数据。

设置 CONSOLE_TEST_DATABASE_URL 后运行；未设置时跳过，不访问业务数据库。
"""

import asyncio
import io
import os
from contextlib import redirect_stdout
import unittest
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import delete, func, insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateSchema, DropSchema

with redirect_stdout(io.StringIO()):
    from console_server.api.v1.endpoints.permission import remove_permission
    from console_server.api.v1.endpoints.role import remove_role, update_role_permissions
    from console_server.api.v1.endpoints.user import update_user_roles
    from console_server.model.common import Base
    from console_server.model.rbac import Permission, Role, User, role_permissions, user_roles
    from console_server.schema.permission import AssignPermissionsRequest
    from console_server.schema.user import UserRolesRequest


class CommitGate:
    """在实际接口提交前暂停事务，让另一接口进入真实行锁等待。"""

    def __init__(self, session):
        self.session = session
        self.ready = asyncio.Event()
        self.release = asyncio.Event()

    def __getattr__(self, name):
        return getattr(self.session, name)

    async def commit(self):
        self.ready.set()
        await self.release.wait()
        await self.session.commit()


@unittest.skipUnless(os.getenv("CONSOLE_TEST_DATABASE_URL"), "未配置独立 PostgreSQL 测试连接")
class DeletionConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.schema = "rbac_test_" + uuid4().hex
        self.engine = create_async_engine(
            os.environ["CONSOLE_TEST_DATABASE_URL"], echo=False,
            connect_args={"server_settings": {"statement_timeout": "5000", "lock_timeout": "4000"}},
        ).execution_options(schema_translate_map={None: self.schema})
        async with self.engine.begin() as connection:
            await connection.execute(CreateSchema(self.schema))
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.sessions.begin() as db:
            db.add_all([
                Role(id=1, name="custom", display_name="测试角色", is_active=True,
                     is_editable=True, is_deletable=True, permissions=[]),
                Permission(id=1, name="api:test:read", display_name="测试权限",
                           is_editable=True, is_deletable=True),
                User(id=1, name="测试用户", email="test@example.com", password="test",
                     is_active=True, is_editable=True, roles=[]),
            ])
        self.actor = User(id=999, is_active=True, roles=[Role(
            name="operator", is_active=True, permissions=[Permission(name="api:*")]
        )])

    async def asyncTearDown(self):
        try:
            async with self.engine.begin() as connection:
                await connection.execute(DropSchema(self.schema, cascade=True))
        finally:
            await self.engine.dispose()

    async def race(self, first_operation, second_operation):
        async with self.sessions() as first_db, self.sessions() as second_db:
            gate = CommitGate(first_db)
            first = asyncio.create_task(first_operation(gate))
            second = None
            try:
                await asyncio.wait_for(gate.ready.wait(), 2)
                second = asyncio.create_task(second_operation(second_db))
                # 必须等待事务提交，不能在引用尚未可见时提前完成。
                with self.assertRaises(asyncio.TimeoutError):
                    await asyncio.wait_for(asyncio.shield(second), 0.15)
                gate.release.set()
                return await asyncio.wait_for(asyncio.gather(first, second, return_exceptions=True), 4)
            finally:
                gate.release.set()
                await asyncio.gather(*([first, second] if second else [first]), return_exceptions=True)

    async def assign_permission(self, db):
        return await update_role_permissions(
            permission_request=AssignPermissionsRequest(permission_ids=[1]),
            role_id=1, current_user=self.actor, db=db,
        )

    async def assign_role(self, db):
        return await update_user_roles(
            user_id=1, role_request=UserRolesRequest(role_ids=[1]),
            current_user=self.actor, db=db,
        )

    async def delete_permission(self, db):
        return await remove_permission(permission_id=1, current_user=self.actor, db=db)

    async def delete_role(self, db):
        return await remove_role(role_id=1, current_user=self.actor, db=db)

    async def test_permission_assignment_commits_before_delete_checks_references(self):
        assigned, deleted = await self.race(self.assign_permission, self.delete_permission)
        self.assertNotIsInstance(assigned, Exception)
        self.assertIsInstance(deleted, HTTPException)
        self.assertEqual((deleted.status_code, deleted.detail["role_count"]), (409, 1))

    async def test_permission_delete_commits_before_assignment_checks_existence(self):
        deleted, assigned = await self.race(self.delete_permission, self.assign_permission)
        self.assertNotIsInstance(deleted, Exception)
        self.assertIsInstance(assigned, HTTPException)
        self.assertEqual(assigned.status_code, 400)
        async with self.sessions() as db:
            self.assertEqual((await db.execute(select(func.count()).select_from(role_permissions))).scalar_one(), 0)

    async def test_role_assignment_commits_before_delete_checks_references(self):
        assigned, deleted = await self.race(self.assign_role, self.delete_role)
        self.assertNotIsInstance(assigned, Exception)
        self.assertIsInstance(deleted, HTTPException)
        self.assertEqual((deleted.status_code, deleted.detail["user_count"]), (409, 1))

    async def test_role_delete_commits_before_assignment_checks_existence(self):
        deleted, assigned = await self.race(self.delete_role, self.assign_role)
        self.assertNotIsInstance(deleted, Exception)
        self.assertIsInstance(assigned, HTTPException)
        self.assertEqual(assigned.status_code, 400)
        async with self.sessions() as db:
            self.assertEqual((await db.execute(select(func.count()).select_from(user_roles))).scalar_one(), 0)

    async def seed_associations(self):
        async with self.sessions.begin() as db:
            await db.execute(insert(user_roles).values(user_id=1, role_id=1))
            await db.execute(insert(role_permissions).values(role_id=1, permission_id=1))

    async def test_foreign_keys_restrict_references_and_cascade_owned_configuration(self):
        await self.seed_associations()
        async with self.sessions() as db:
            for entity in [Role, Permission]:
                with self.assertRaises(IntegrityError):
                    await db.execute(delete(entity).where(entity.id == 1))
                await db.rollback()
            await db.execute(delete(User).where(User.id == 1))
            await db.commit()
            self.assertEqual((await db.execute(select(func.count()).select_from(user_roles))).scalar_one(), 0)
            self.assertEqual((await db.execute(select(func.count()).select_from(role_permissions))).scalar_one(), 1)
            await db.execute(delete(Role).where(Role.id == 1))
            await db.commit()
            self.assertEqual((await db.execute(select(func.count()).select_from(role_permissions))).scalar_one(), 0)
            self.assertIsNotNone((await db.execute(select(Permission).where(Permission.id == 1))).scalar_one_or_none())
