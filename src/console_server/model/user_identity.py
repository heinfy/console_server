"""用户鉴权身份只创建一次，删除账号后仍保留 UUID 占用记录。"""

from sqlalchemy import Column, DateTime, Table, Uuid, func

from .common import Base


user_identities = Table(
    "user_identities",
    Base.metadata,
    Column("uuid", Uuid(as_uuid=True), primary_key=True),
    Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
)


POSTGRESQL_UUID_GUARDS = """
DO $guards$
BEGIN
    CREATE OR REPLACE FUNCTION __schema__.guard_user_uuid()
    RETURNS trigger LANGUAGE plpgsql AS $function$
    BEGIN
        IF TG_OP = 'INSERT' THEN
            NEW.uuid := COALESCE(NEW.uuid, gen_random_uuid());
            INSERT INTO __schema__.user_identities (uuid) VALUES (NEW.uuid);
        ELSIF NEW.uuid IS DISTINCT FROM OLD.uuid THEN
            RAISE EXCEPTION 'User UUID is immutable' USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END;
    $function$;

    CREATE OR REPLACE FUNCTION __schema__.guard_user_identity_history()
    RETURNS trigger LANGUAGE plpgsql AS $function$
    BEGIN
        RAISE EXCEPTION 'User identity history is append-only' USING ERRCODE = '23514';
    END;
    $function$;

    DROP TRIGGER IF EXISTS users_uuid_guard ON __schema__.users;
    CREATE TRIGGER users_uuid_guard
        BEFORE INSERT OR UPDATE OF uuid ON __schema__.users
        FOR EACH ROW EXECUTE FUNCTION __schema__.guard_user_uuid();

    DROP TRIGGER IF EXISTS user_identities_history_guard ON __schema__.user_identities;
    CREATE TRIGGER user_identities_history_guard
        BEFORE UPDATE OR DELETE ON __schema__.user_identities
        FOR EACH ROW EXECUTE FUNCTION __schema__.guard_user_identity_history();

    DROP TRIGGER IF EXISTS user_identities_truncate_guard ON __schema__.user_identities;
    CREATE TRIGGER user_identities_truncate_guard
        BEFORE TRUNCATE ON __schema__.user_identities
        FOR EACH STATEMENT EXECUTE FUNCTION __schema__.guard_user_identity_history();
END;
$guards$;
"""


def install_user_uuid_guards(table, connection, **kwargs):
    """ORM 建表时安装身份约束，覆盖 ORM 和直接 SQL 写入。"""
    schema = connection.schema_for_object(table)
    if connection.dialect.name == "postgresql":
        schema = schema or connection.dialect.default_schema_name
        quoted_schema = connection.dialect.identifier_preparer.quote(schema)
        connection.exec_driver_sql(POSTGRESQL_UUID_GUARDS.replace("__schema__", quoted_schema))
    elif connection.dialect.name == "sqlite":
        for statement in (
            """CREATE TRIGGER users_reserve_uuid AFTER INSERT ON users
               BEGIN INSERT INTO user_identities (uuid) VALUES (NEW.uuid); END""",
            """CREATE TRIGGER users_uuid_immutable BEFORE UPDATE OF uuid ON users
               WHEN NEW.uuid IS NOT OLD.uuid
               BEGIN SELECT RAISE(ABORT, 'User UUID is immutable'); END""",
            """CREATE TRIGGER user_identities_no_update BEFORE UPDATE ON user_identities
               BEGIN SELECT RAISE(ABORT, 'User identity history is append-only'); END""",
            """CREATE TRIGGER user_identities_no_delete BEFORE DELETE ON user_identities
               BEGIN SELECT RAISE(ABORT, 'User identity history is append-only'); END""",
        ):
            connection.exec_driver_sql(statement)
