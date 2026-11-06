"""批量统计授权引用，避免列表序列化触发异步懒加载或逐条查询。"""

from sqlalchemy import func, select

from console_server.model.rbac import role_permissions, user_roles
from console_server.schema.permission import PermissionInfoResponse
from console_server.schema.role import RoleResponse


async def association_counts(db, column, ids):
    if not ids:
        return {}
    result = await db.execute(
        select(column, func.count()).where(column.in_(ids)).group_by(column)
    )
    return dict(result.all())


async def permission_responses(db, permissions):
    counts = await association_counts(
        db, role_permissions.c.permission_id, [permission.id for permission in permissions]
    )
    return [
        PermissionInfoResponse.model_validate(permission).model_copy(
            update={"role_count": counts.get(permission.id, 0)}
        )
        for permission in permissions
    ]


async def role_responses(db, roles):
    counts = await association_counts(db, user_roles.c.role_id, [role.id for role in roles])
    return [
        RoleResponse.model_validate(role).model_copy(update={"user_count": counts.get(role.id, 0)})
        for role in roles
    ]
