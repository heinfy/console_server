"""真实 PostgreSQL 中验证行锁轮换、会话撤销和事务回滚。"""

import io
import os
import asyncio
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException, Request
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.schema import CreateSchema, DropSchema

with redirect_stdout(io.StringIO()):
    from console_server.model.common import Base
    from console_server.model.rbac import User
    from console_server.model.auth_session import AuthSession
    from console_server.utils.auth import (start_session, rotate_session, get_current_user, decode_token,
                                           revoke_presented_sessions, add_token_to_blacklist)


@unittest.skipUnless(os.getenv('CONSOLE_TEST_DATABASE_URL'), '未配置 PostgreSQL 测试连接')
class PostgreSQLSessionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.schema = 'session_test_' + uuid4().hex
        self.engine = create_async_engine(os.environ['CONSOLE_TEST_DATABASE_URL'], echo=False, hide_parameters=True)
        async with self.engine.begin() as conn:
            await conn.execute(CreateSchema(self.schema))
        translated = self.engine.execution_options(schema_translate_map={None: self.schema})
        async with translated.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(translated, expire_on_commit=False)
        async with self.sessions() as db:
            user = User(name='session', email='session@example.com', password='original-hash', is_active=True)
            db.add(user)
            await db.flush()
            self.user_uuid = user.uuid
            self.session, self.tokens = await start_session(user, db)
            await db.commit()

    async def asyncTearDown(self):
        try:
            async with self.engine.begin() as conn:
                await conn.execute(DropSchema(self.schema, cascade=True))
        finally:
            await self.engine.dispose()

    async def test_refresh_replay_is_atomic_across_connections(self):
        import asyncio
        async def refresh():
            async with self.sessions() as db:
                return await rotate_session(self.tokens['refresh_token'], db)
        results = await asyncio.gather(refresh(), refresh(), return_exceptions=True)
        succeeded = [r for r in results if isinstance(r, tuple)]
        rejected = [r for r in results if isinstance(r, HTTPException)]
        self.assertEqual((len(succeeded), len(rejected)), (1, 1), results)
        self.assertEqual(rejected[0].status_code, 401)
        async with self.sessions() as db:
            session = await db.get(AuthSession, self.session.id)
            self.assertIsNotNone(session.revoked_at)
            with self.assertRaises(HTTPException):
                await get_current_user(succeeded[0][1]['access_token'], db)

    async def test_password_and_disable_revoke_sessions_at_database_level(self):
        async with self.sessions() as db:
            await db.execute(update(User).where(User.uuid == self.user_uuid).values(password='changed-hash'))
            await db.commit()
            self.assertIsNotNone((await db.get(AuthSession, self.session.id)).revoked_at)
            user = (await db.execute(select(User).where(User.uuid == self.user_uuid))).scalar_one()
            session, tokens = await start_session(user, db)
            await db.commit()
            await db.execute(update(User).where(User.uuid == self.user_uuid).values(is_active=False))
            await db.commit()
            await db.execute(update(User).where(User.uuid == self.user_uuid).values(is_active=True))
            await db.commit()
        async with self.sessions() as db:
            self.assertIsNotNone((await db.get(AuthSession, session.id)).revoked_at)
            with self.assertRaises(HTTPException):
                await get_current_user(tokens['access_token'], db)

    async def test_failed_refresh_commit_rolls_back_rotation(self):
        async with self.sessions() as db:
            with patch.object(db, 'commit', side_effect=RuntimeError('commit failed')):
                with self.assertRaises(RuntimeError):
                    await rotate_session(self.tokens['refresh_token'], db)
            await db.rollback()
        async with self.sessions() as db:
            session = await db.get(AuthSession, self.session.id)
            self.assertEqual(str(session.refresh_jti), decode_token(self.tokens['refresh_token'], 'refresh')['jti'])
            _, replacement = await rotate_session(self.tokens['refresh_token'], db)
            self.assertNotEqual(replacement['refresh_token'], self.tokens['refresh_token'])


    async def test_logout_waits_for_in_flight_rotation_then_revokes_its_result(self):
        class CommitGate:
            def __init__(self, db):
                self.db = db
                self.ready = asyncio.Event()
                self.release = asyncio.Event()
            def __getattr__(self, name):
                return getattr(self.db, name)
            async def commit(self):
                self.ready.set()
                await self.release.wait()
                await self.db.commit()
        async with self.sessions() as refresh_db, self.sessions() as logout_db:
            gate = CommitGate(refresh_db)
            rotating = asyncio.create_task(rotate_session(self.tokens['refresh_token'], gate))
            exiting = None
            try:
                await asyncio.wait_for(gate.ready.wait(), 2)
                request = Request({'type': 'http', 'headers': [
                    (b'authorization', ('Bearer ' + self.tokens['access_token']).encode())
                ]})
                exiting = asyncio.create_task(revoke_presented_sessions(request, logout_db))
                with self.assertRaises(asyncio.TimeoutError):
                    await asyncio.wait_for(asyncio.shield(exiting), 0.15)
                gate.release.set()
                rotation, _ = await asyncio.wait_for(asyncio.gather(rotating, exiting), 4)
            finally:
                gate.release.set()
                await asyncio.gather(*([rotating, exiting] if exiting else [rotating]), return_exceptions=True)
        async with self.sessions() as db:
            self.assertIsNotNone((await db.get(AuthSession, self.session.id)).revoked_at)
            with self.assertRaises(HTTPException):
                await get_current_user(rotation[1]['access_token'], db)
            with self.assertRaises(HTTPException):
                await rotate_session(rotation[1]['refresh_token'], db)

    async def test_blacklisted_refresh_revokes_its_entire_family(self):
        async with self.sessions() as db:
            await add_token_to_blacklist(self.tokens['refresh_token'], db)
        async with self.sessions() as db:
            with self.assertRaises(HTTPException) as failure:
                await rotate_session(self.tokens['refresh_token'], db)
            self.assertEqual(failure.exception.status_code, 401)
        async with self.sessions() as db:
            self.assertIsNotNone((await db.get(AuthSession, self.session.id)).revoked_at)
            with self.assertRaises(HTTPException):
                await get_current_user(self.tokens['access_token'], db)
