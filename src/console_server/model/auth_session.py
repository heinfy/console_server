"""持久化登录会话，保存当前刷新 jti；不保存原始令牌。"""

from uuid import uuid4

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Uuid, event
from sqlalchemy.sql import func

from .common import Base


def session_revocation_sql(schema: str) -> str:
    namespace = '"' + schema.replace('"', '""') + '"'
    return f"""
CREATE OR REPLACE FUNCTION {namespace}.revoke_changed_user_sessions()
RETURNS trigger LANGUAGE plpgsql AS $session_guard$
BEGIN
    IF NEW.password IS DISTINCT FROM OLD.password
       OR (OLD.is_active AND NOT NEW.is_active) THEN
        UPDATE {namespace}.auth_sessions
        SET revoked_at = CURRENT_TIMESTAMP
        WHERE user_uuid = OLD.uuid AND revoked_at IS NULL;
    END IF;
    RETURN NEW;
END;
$session_guard$;
DROP TRIGGER IF EXISTS users_revoke_sessions ON {namespace}.users;
CREATE TRIGGER users_revoke_sessions
AFTER UPDATE OF password, is_active ON {namespace}.users
FOR EACH ROW EXECUTE FUNCTION {namespace}.revoke_changed_user_sessions();
"""


def install_session_guards(table, connection, **kwargs) -> None:
    if connection.dialect.name == "postgresql":
        schema = connection.schema_for_object(table) or "public"
        # asyncpg 不接受多条预编译语句；在单个 DO 块中执行 DDL。
        sql = session_revocation_sql(schema)
        connection.exec_driver_sql("DO $install$ BEGIN " + sql + " END; $install$;")
    elif connection.dialect.name == "sqlite":
        connection.exec_driver_sql("""
            CREATE TRIGGER IF NOT EXISTS users_revoke_sessions
            AFTER UPDATE OF password, is_active ON users
            WHEN NEW.password IS NOT OLD.password OR (OLD.is_active = 1 AND NEW.is_active = 0)
            BEGIN
                UPDATE auth_sessions SET revoked_at = CURRENT_TIMESTAMP
                WHERE user_uuid = OLD.uuid AND revoked_at IS NULL;
            END
        """)


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    id = Column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    user_uuid = Column(Uuid(as_uuid=True), ForeignKey("users.uuid", ondelete="CASCADE"),
                       nullable=False, index=True)
    refresh_jti = Column(Uuid(as_uuid=True), unique=True, nullable=False)
    persistent = Column(Boolean, nullable=False, default=False, server_default="false")
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


event.listen(AuthSession.__table__, "after_create", install_session_guards)
