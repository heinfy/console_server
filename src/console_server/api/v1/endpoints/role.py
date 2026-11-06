from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy.orm import selectinload
from typing import cast

from console_server.core.constants import (
    ROLE_DELETE_API,
    ROLE_GET_API,
    ROLE_PATH,
    ROLE_POST_API,
    ROLE_PUT_API,
    ROLE_ASSIGN_PERMISSION_API,
    USER_GET_API,
    USER_ASSIGN_ROLE_API,
)
from console_server.db import database
from console_server.model.rbac import User, Role, Permission, AuthorizationAudit, role_permissions, user_roles
from console_server.schema.common import PaginatedResponse, SuccessResponse
from console_server.schema.user import UserInfoResponse
from console_server.utils.associations import permission_responses, role_responses
from console_server.schema.role import (
    RoleCreate,
    RoleListResponse,
    RoleResponse,
    RolePermissionResponse,
    RoleUpdateRequest,
    RolePermissionOptionsResponse,
)
from console_server.schema.permission import AssignPermissionsRequest
from console_server.core.config import settings
from console_server.schema.search import RoleSearchParams
from console_server.utils.search import build_search_filters


from console_server.utils.auth import (
    require_permission, require_any_permission, ensure_grantable, permission_names,
)
from console_server.core.permissions import can_grant


router = APIRouter(prefix=f"/{ROLE_PATH}", tags=[ROLE_PATH])


# 创建角色
@router.post(
    "/create",
    summary="创建角色",
    description="创建新角色",
    response_model=SuccessResponse,
)
async def create_role(
    role: RoleCreate,
    current_user: User = Depends(require_permission(ROLE_PATH, ROLE_POST_API)),
    db: AsyncSession = Depends(database.get_db),
) -> SuccessResponse:
    """创建可编辑、可删除的自定义角色，并校验角色标识的唯一性。"""
    # 检查角色名称是否已存在
    result = await db.execute(select(Role).where(Role.name == role.name))
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Role name already exists"
        )
    # 创建新角色
    new_role = Role(
        name=role.name,
        display_name=role.display_name,
        description=role.description,
        is_active=role.is_active,
        # 系统角色保留模型默认保护标记，自定义角色允许后续编辑和删除。
        is_editable=True,
        is_deletable=True,
    )
    db.add(new_role)
    try:
        await db.commit()
    except IntegrityError:
        # 并发创建可能绕过前置查询；回滚后确认是否发生角色标识冲突。
        await db.rollback()
        result = await db.execute(select(Role).where(Role.name == role.name))
        if result.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Role name already exists"
            )
        raise
    await db.refresh(new_role)
    return SuccessResponse()


async def _get_role_for_permission_update(role_id: int, db: AsyncSession) -> Role:
    """锁定目标角色并检查保护标记，统一约束追加及替换权限的操作。"""
    result = await db.execute(
        select(Role)
        .where(Role.id == role_id)
        .with_for_update()
        .execution_options(populate_existing=True)
        .options(selectinload(Role.permissions))
    )
    role = result.scalar_one_or_none()
    if not role:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Role not found"
        )
    if role.name == "admin" or not role.is_editable:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Role permissions cannot be changed",
        )
    return role


async def _get_permissions_by_ids(
    permission_ids: list[int], db: AsyncSession
) -> list[Permission]:
    """先验证全部权限存在，再返回去重后的权限对象，避免部分关联写入。"""
    result = await db.execute(
        select(Permission).where(Permission.id.in_(permission_ids)).order_by(Permission.id)
        .with_for_update().execution_options(populate_existing=True)
    )
    permissions = list(result.scalars().all())
    found_ids = {cast(int, permission.id) for permission in permissions}
    if set(permission_ids) - found_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Permission not found"
        )
    return permissions


# 给角色追加权限，保留已有接口的追加语义。
@router.post(
    "/{role_id}/assign-permissions",
    summary="为角色分配多个权限（通过ID）",
    description="根据权限ID列表为特定角色分配权限。",
    response_model=SuccessResponse,
)
async def assign_permissions_to_role(
    permission_request: AssignPermissionsRequest,
    role_id: int = Path(ge=1, description="角色 ID"),
    current_user: User = Depends(require_permission(ROLE_PATH, ROLE_ASSIGN_PERMISSION_API)),
    db: AsyncSession = Depends(database.get_db),
) -> SuccessResponse:
    """为可编辑角色追加权限，重复提交不会产生重复关联。"""
    role = await _get_role_for_permission_update(role_id, db)
    # 先按 ID 锁定新旧权限，与权限删除及其他角色授权保持统一顺序。
    locked_permissions = await _get_permissions_by_ids(
        sorted({p.id for p in role.permissions} | set(permission_request.permission_ids)), db
    )
    permissions_to_assign = [p for p in locked_permissions if p.id in permission_request.permission_ids]
    ensure_grantable(current_user, [*role.permissions, *permissions_to_assign])
    existing_permission_ids = {cast(int, p.id) for p in role.permissions}
    new_permissions = [
        p
        for p in permissions_to_assign
        if cast(int, p.id) not in existing_permission_ids
    ]

    if new_permissions:
        before_ids = sorted(existing_permission_ids)
        role.permissions.extend(new_permissions)
        role.updated_at = func.now()  # type: ignore
        db.add(AuthorizationAudit(
            actor_id=current_user.id, target_type="role", target_id=role.id,
            action="assign-permission", before=before_ids,
            after=sorted(p.id for p in role.permissions),
        ))
    await db.commit()
    return SuccessResponse()


# 一次保存完整权限集合，支持新增关联、取消关联和清空。
@router.put(
    "/{role_id}/permissions",
    summary="设置角色权限",
    description="用提交的权限 ID 列表替换当前角色权限，空列表表示清空全部关联",
    response_model=SuccessResponse,
)
async def update_role_permissions(
    permission_request: AssignPermissionsRequest,
    role_id: int = Path(ge=1, description="角色 ID"),
    current_user: User = Depends(require_permission(ROLE_PATH, ROLE_ASSIGN_PERMISSION_API)),
    db: AsyncSession = Depends(database.get_db),
) -> SuccessResponse:
    """在单个事务中替换角色权限；不删除权限实体或角色的用户关联。

    Args:
        permission_request: 保存后应关联的完整权限 ID 列表。
        role_id: 待设置权限的角色 ID。
        current_user: 拥有角色权限分配能力的当前用户。
        db: 数据库异步会话。

    Raises:
        HTTPException: 角色或权限不存在、角色受保护或关联写入冲突时返回 400。
    """
    role = await _get_role_for_permission_update(role_id, db)
    locked_permissions = await _get_permissions_by_ids(
        sorted({p.id for p in role.permissions} | set(permission_request.permission_ids)), db
    )
    permissions = [p for p in locked_permissions if p.id in permission_request.permission_ids]
    ensure_grantable(current_user, [*role.permissions, *permissions])
    current_ids = {cast(int, permission.id) for permission in role.permissions}
    selected_ids = {cast(int, permission.id) for permission in permissions}
    if current_ids != selected_ids:
        role.permissions = permissions
        role.updated_at = func.now()  # type: ignore
        db.add(AuthorizationAudit(
            actor_id=current_user.id, target_type="role", target_id=role.id,
            action="assign-permission", before=sorted(current_ids), after=sorted(selected_ids),
        ))
    try:
        await db.commit()
    except IntegrityError as exc:
        # 并发删除权限等情况不能留下部分更新，允许刷新后重试。
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Role permissions changed, please retry",
        ) from exc
    return SuccessResponse()


# 获取角色列表，支持分页
@router.get(
    "/list",
    summary="获取角色列表",
    description="获取所有角色的列表（需要登录，支持分页）",
    response_model=RoleListResponse,
)
async def list_roles(
    current_user: User = Depends(require_permission(ROLE_PATH, ROLE_GET_API)),
    db: AsyncSession = Depends(database.get_db),
    page: int = Query(1, ge=1, description="页码，从1开始"),
    page_size: int = Query(
        settings.DEFAULT_PAGE_SIZE,
        ge=1,
        le=settings.MAX_PAGE_SIZE,
        description="每页数量，最大100",
    ),
    search: RoleSearchParams = Depends(),
) -> RoleListResponse:
    """
    获取角色列表接口（支持组合搜索和分页）。

    需要有效的 JWT token 及角色查询权限。

    参数：
    - page: 页码，从 1 开始，默认为 1。
    - page_size: 每页数量，默认值和上限由分页配置决定。
    - search: 标识、显示名称、关键词、启用状态及编辑/删除标记。

    返回：
    - 当前页角色列表、角色总数、页码、每页数量及总页数。
    """
    # 计算当前页的查询偏移量。
    offset = (page - 1) * page_size

    # 查询角色总数，用于计算分页信息。
    filters = build_search_filters(Role, search, ("name", "display_name", "description"))
    count_result = await db.execute(
        select(func.count()).select_from(Role).where(*filters)
    )
    total = count_result.scalar_one()

    # 按唯一 ID 固定排序，保证数据未变更时各页结果的顺序稳定。
    result = await db.execute(
        select(Role).where(*filters).order_by(Role.id).offset(offset).limit(page_size)
    )
    roles = result.scalars().all()

    # 计算总页数
    total_pages = (total + page_size - 1) // page_size

    # 转换为响应模型，仅返回角色列表所需字段。
    return RoleListResponse(
        items=await role_responses(db, roles),
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


# 获取单个角色详情
@router.get(
    "/{role_id}/detail",
    summary="获取角色详情",
    description="根据角色 ID 获取角色信息、操作保护标记及审计时间",
    response_model=RoleResponse,
)
async def get_role_detail(
    role_id: int = Path(ge=1, description="角色 ID"),
    current_user: User = Depends(require_permission(ROLE_PATH, ROLE_GET_API)),
    db: AsyncSession = Depends(database.get_db),
) -> RoleResponse:
    """查询单个角色；目标不存在时返回明确的错误信息。"""
    result = await db.execute(select(Role).where(Role.id == role_id))
    role = result.scalar_one_or_none()
    if not role:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Role not found"
        )
    return (await role_responses(db, [role]))[0]


# 移除角色
@router.delete(
    "/{role_id}/remove",
    summary="移除角色",
    description="仅删除未关联用户的角色，同时清理该角色自己的权限配置",
    response_model=SuccessResponse,
)
async def remove_role(
    role_id: int = Path(ge=1, description="角色 ID"),
    current_user: User = Depends(require_permission(ROLE_PATH, ROLE_DELETE_API)),
    db: AsyncSession = Depends(database.get_db),
) -> SuccessResponse:
    """禁止删除仍关联用户的角色；无用户引用时清理角色及其权限配置。"""
    # 锁定角色并读取权限用于授权范围校验及审计；引用数量在获得锁后另行查询。
    result = await db.execute(
        select(Role)
        .where(Role.id == role_id)
        .with_for_update()
        .execution_options(populate_existing=True)
        .options(selectinload(Role.permissions))
    )
    role = result.scalar_one_or_none()
    if not role:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Role not found"
        )
    if role.name == "admin" or not role.is_deletable:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Role cannot be deleted"
        )
    ensure_grantable(current_user, role.permissions)
    result = await db.execute(
        select(func.count()).select_from(user_roles).where(user_roles.c.role_id == role_id)
    )
    user_count = result.scalar_one()
    if user_count:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={
            "code": "role_in_use",
            "message": f"该角色已关联 {user_count} 个用户，请先解除关联后再删除",
            "user_count": user_count,
        })
    await _get_permissions_by_ids(sorted(p.id for p in role.permissions), db)

    db.add(AuthorizationAudit(
        actor_id=current_user.id, target_type="role", target_id=role.id,
        action="delete", before={"name": role.name, "display_name": role.display_name,
                                 "permission_ids": sorted(p.id for p in role.permissions),
                                 "permissions": [{"id": p.id, "name": p.name, "display_name": p.display_name}
                                                 for p in sorted(role.permissions, key=lambda p: p.id)],
                                 "user_ids": []}, after={},
    ))
    try:
        await db.execute(delete(role_permissions).where(role_permissions.c.role_id == role_id))
        await db.execute(delete(Role).where(Role.id == role_id))
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={
            "code": "role_in_use", "message": "角色引用已变化，请刷新并解除关联后重试",
        }) from exc
    return SuccessResponse()


# 更新角色
@router.put(
    "/{role_id}/update",
    summary="更新角色",
    description="更新某个角色",
    response_model=SuccessResponse,
)
async def update_role(
    role_update: RoleUpdateRequest,
    role_id: int = Path(ge=1, description="角色 ID"),
    current_user: User = Depends(require_permission(ROLE_PATH, ROLE_PUT_API)),
    db: AsyncSession = Depends(database.get_db),
) -> SuccessResponse:
    """更新自定义角色信息及启用状态，不允许修改受保护角色。"""
    result = await db.execute(
        select(Role).where(Role.id == role_id).with_for_update()
        .execution_options(populate_existing=True)
        .options(selectinload(Role.permissions))
    )
    role = result.scalar_one_or_none()
    if not role:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Role not found"
        )

    if role.name == "admin" or not role.is_editable:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Role cannot be edited"
        )
    ensure_grantable(current_user, role.permissions)
    if bool(role.is_active) != role_update.is_active:
        db.add(AuthorizationAudit(
            actor_id=current_user.id, target_type="role", target_id=role.id,
            action="set-active", before={"is_active": bool(role.is_active)},
            after={"is_active": role_update.is_active},
        ))

    role.display_name = role_update.display_name  # type: ignore
    role.description = role_update.description  # type: ignore
    role.is_active = role_update.is_active  # type: ignore

    db.add(role)
    await db.commit()
    await db.refresh(role)
    return SuccessResponse()


@router.get("/{role_id}/permission-options", response_model=RolePermissionOptionsResponse)
async def get_role_permission_options(
    role_id: int = Path(ge=1),
    current_user: User = Depends(require_any_permission(ROLE_GET_API, ROLE_ASSIGN_PERMISSION_API)),
    db: AsyncSession = Depends(database.get_db),
) -> RolePermissionOptionsResponse:
    """只返回已关联和可授予的权限，避免依赖全局权限目录读取能力。"""
    result = await db.execute(select(Role).where(Role.id == role_id).options(selectinload(Role.permissions)))
    role = result.scalar_one_or_none()
    if role is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Role not found")
    result = await db.execute(select(Permission).order_by(Permission.id))
    permissions = list(result.scalars().all())
    names = permission_names(current_user)
    can_assign = can_grant(names, ROLE_ASSIGN_PERMISSION_API)
    grantable_ids = {p.id for p in permissions if can_assign and can_grant(names, p.name)}
    assigned_ids = {p.id for p in role.permissions}
    return RolePermissionOptionsResponse(
        role=(await role_responses(db, [role]))[0],
        permissions=await permission_responses(db, [p for p in permissions
                     if p.id in assigned_ids or p.id in grantable_ids]),
        permission_ids=sorted(assigned_ids), grantable_permission_ids=sorted(grantable_ids),
        editable=bool(can_assign and role.is_editable and role.name != "admin"
                      and assigned_ids.issubset(grantable_ids)),
    )


@router.get("/{role_id}/users", response_model=PaginatedResponse[UserInfoResponse])
async def get_role_users(
    role_id: int = Path(ge=1),
    current_user: User = Depends(require_permission(ROLE_PATH, ROLE_GET_API)),
    user_reader: User = Depends(require_any_permission(USER_GET_API, USER_ASSIGN_ROLE_API)),
    db: AsyncSession = Depends(database.get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(settings.DEFAULT_PAGE_SIZE, ge=1, le=settings.MAX_PAGE_SIZE),
) -> PaginatedResponse[UserInfoResponse]:
    """查看关联用户；用户查询或角色分配能力为额外边界，不返回账号凭据。"""
    if (await db.execute(select(Role.id).where(Role.id == role_id))).scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Role not found")
    condition = user_roles.c.role_id == role_id
    total = (await db.execute(select(func.count()).select_from(user_roles).where(condition))).scalar_one()
    result = await db.execute(
        select(User).join(user_roles, User.id == user_roles.c.user_id)
        .where(condition).order_by(User.id).offset((page - 1) * page_size).limit(page_size)
        .options(selectinload(User.roles))
    )
    users = list(result.scalars().all())
    items = [UserInfoResponse(
        id=user.id, name=user.name, email=user.email, description=user.description,
        is_active=user.is_active,
        is_editable=bool(user.is_editable and all(role.name != "admin" for role in user.roles)),
        is_deletable=bool(user.is_deletable and all(role.name != "admin" for role in user.roles)),
    ) for user in users]
    return PaginatedResponse[UserInfoResponse](
        items=items, total=total, page=page, page_size=page_size,
        total_pages=(total + page_size - 1) // page_size,
    )


# 根据角色ID获取权限列表
@router.get(
    "/{role_id}/permissions",
    summary="获取角色的权限列表",
    description="根据角色ID获取该角色分配的权限列表",
    response_model=RolePermissionResponse,
)
async def get_role_permissions(
    role_id: int = Path(ge=1, description="角色 ID"),
    current_user: User = Depends(require_permission(ROLE_PATH, ROLE_GET_API)),
    db: AsyncSession = Depends(database.get_db),
) -> RolePermissionResponse:
    """查询角色已关联的权限 ID，按 ID 升序返回以便稳定回显。"""
    result = await db.execute(
        select(Role).where(Role.id == role_id).options(selectinload(Role.permissions))
    )
    role = result.scalar_one_or_none()
    if not role:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Role not found"
        )
    return RolePermissionResponse(
        role_id=cast(int, role.id),
        permission_ids=sorted(cast(int, permission.id) for permission in role.permissions),
    )
