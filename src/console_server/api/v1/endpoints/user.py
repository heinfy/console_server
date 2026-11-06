from typing import List, cast
from sqlalchemy import func, select, update
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from fastapi import APIRouter, Depends, HTTPException, Query, status, Body

from console_server.core.constants import (
    USER_PATH,
    USER_PUT_API,
    USER_POST_API,
    USER_GET_API,
    USER_DELETE_API,
    USER_ASSIGN_ROLE_API,
)

from console_server.db import database
from console_server.model.rbac import User, Role, AuthorizationAudit
from console_server.schema.common import SuccessResponse
from console_server.schema.permission import PermissionResponse
from console_server.schema.user import (
    UserInfoResponse,
    UserListResponse,
    DisableUserRequest,
    UserCreateRequest,
    UserUpdateRequest,
    UserDetailResponse,
    UserRolesRequest,
    UserRoleOptionsResponse,
)
from console_server.schema.role import (
    AssignRolesRequest,
    RemoveRolesRequest,
    UserRoleResponse,
)
from console_server.utils.auth import (
    get_password_hash, require_permission, require_any_permission,
    ensure_grantable, permission_names,
)
from console_server.core.permissions import can_grant
from console_server.core.config import settings
from console_server.schema.search import UserSearchParams
from console_server.utils.search import build_search_filters
from console_server.utils.associations import role_responses


router = APIRouter(prefix=f"/{USER_PATH}", tags=[USER_PATH])


async def _get_user_or_404(user_id: int, db: AsyncSession, *, for_update: bool = False) -> User:
    """查询用户并预加载角色；用户不存在时返回 404。"""
    query = (
        select(User).where(User.id == user_id)
        .options(selectinload(User.roles).selectinload(Role.permissions))
    )
    if for_update:
        query = query.with_for_update().execution_options(populate_existing=True)
    result = await db.execute(query)
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")
    if for_update:
        await _get_roles_by_ids([cast(int, role.id) for role in user.roles], db, for_update=True)
    return user


def _user_info(user: User) -> UserInfoResponse:
    """构建用户展示信息，对管理员账号统一执行编辑和删除保护。"""
    is_admin = any(role.name == "admin" for role in user.roles)
    return UserInfoResponse(
        id=cast(int, user.id),
        name=cast(str, user.name),
        email=cast(str, user.email),
        description=cast(str | None, user.description),
        is_active=cast(bool, user.is_active),
        is_deletable=bool(user.is_deletable) and not is_admin,
        is_editable=bool(user.is_editable) and not is_admin,
    )


def _user_detail(user: User) -> UserDetailResponse:
    """构建详情响应，仅序列化已加载的用户字段和角色。"""
    return UserDetailResponse(
        **_user_info(user).model_dump(),
        roles=[
            UserRoleResponse(
                id=cast(int, role.id),
                name=cast(str, role.name),
                display_name=cast(str, role.display_name),
            )
            for role in user.roles
        ],
        created_at=user.created_at,
        updated_at=user.updated_at,
    )


def _ensure_editable(user: User, current_user: User, is_active: bool) -> None:
    """验证账号编辑保护，并禁止操作者停用当前登录账号。"""
    if not _user_info(user).is_editable:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="该用户不可编辑")
    if user.id == current_user.id and not is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="不能停用当前登录账号"
        )
    ensure_grantable(current_user, [p for role in user.roles for p in role.permissions])


async def _commit_user_changes(db: AsyncSession) -> None:
    """提交用户写入；并发写入发生唯一约束冲突时回滚事务。"""
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="用户信息冲突，邮箱可能已被注册",
        ) from exc


@router.post(
    "/create",
    summary="新增用户",
    description="创建用户并分配普通角色，需要用户新增权限。",
    response_model=UserDetailResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_user(
    user_request: UserCreateRequest,
    current_user: User = Depends(require_permission(USER_PATH, USER_POST_API)),
    db: AsyncSession = Depends(database.get_db),
) -> UserDetailResponse:
    """新增用户，验证邮箱唯一性并以哈希形式保存初始密码。"""
    result = await db.execute(
        select(User).where(func.lower(User.email) == str(user_request.email).lower())
    )
    if result.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="邮箱已被注册"
        )

    # 优先使用注册接口约定的 user 角色，兼容初始化 SQL 中的 normal 角色。
    role_result = await db.execute(
        select(Role).where(Role.name.in_(["user", "normal"]), Role.is_active.is_(True))
        .order_by(Role.id).with_for_update().execution_options(populate_existing=True)
    )
    roles = {cast(str, role.name): role for role in role_result.scalars().all()}
    default_role = roles.get("user") or roles.get("normal")
    if default_role is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="请先配置启用的普通用户角色"
        )

    user = User(
        name=user_request.name,
        email=str(user_request.email),
        password=get_password_hash(user_request.password),
        description=user_request.description,
        is_active=user_request.is_active,
        is_editable=True,
        is_deletable=True,
        roles=[default_role],
    )
    db.add(user)
    await _commit_user_changes(db)
    await db.refresh(user)
    # refresh 后重新预加载角色，避免异步序列化触发懒加载。
    return _user_detail(await _get_user_or_404(cast(int, user.id), db))


@router.put(
    "/{user_id}/update",
    summary="编辑用户",
    description="编辑姓名、邮箱、描述、状态及可选密码，需要用户编辑权限。",
    response_model=UserDetailResponse,
)
async def update_user(
    user_id: int,
    user_request: UserUpdateRequest,
    current_user: User = Depends(require_permission(USER_PATH, USER_PUT_API)),
    db: AsyncSession = Depends(database.get_db),
) -> UserDetailResponse:
    """编辑用户，保留未提交的密码并验证账号保护和邮箱唯一性。"""
    user = await _get_user_or_404(user_id, db, for_update=True)
    _ensure_editable(user, current_user, user_request.is_active)
    result = await db.execute(
        select(User).where(
            func.lower(User.email) == str(user_request.email).lower(),
            User.id != user_id,
        )
    )
    if result.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="邮箱已被注册"
        )

    values = user_request.model_dump(exclude={"password"})
    if bool(user.is_active) != user_request.is_active:
        db.add(AuthorizationAudit(
            actor_id=current_user.id, target_type="user", target_id=user.id,
            action="set-active", before={"is_active": bool(user.is_active)},
            after={"is_active": user_request.is_active},
        ))
    if user_request.password is not None:
        values["password"] = get_password_hash(user_request.password)
    # 交由 ORM 在提交时统一写入，使唯一约束冲突进入同一回滚路径。
    for field, value in values.items():
        setattr(user, field, value)
    db.add(user)
    await _commit_user_changes(db)
    await db.refresh(user)
    return _user_detail(await _get_user_or_404(user_id, db))


@router.delete(
    "/{user_id}/remove",
    summary="删除用户",
    description="删除用户及其角色关联，需要用户删除权限。",
    response_model=SuccessResponse,
)
async def remove_user(
    user_id: int,
    current_user: User = Depends(require_permission(USER_PATH, USER_DELETE_API)),
    db: AsyncSession = Depends(database.get_db),
) -> SuccessResponse:
    """删除可删除用户，禁止删除当前登录账号和受保护账号。"""
    user = await _get_user_or_404(user_id, db, for_update=True)
    if not _user_info(user).is_deletable:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="该用户不可删除")
    if user.id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="不能删除当前登录账号"
        )
    # ORM 删除用户时同步清理多对多关联，保留共享的角色记录。
    ensure_grantable(current_user, [p for role in user.roles for p in role.permissions])
    db.add(AuthorizationAudit(
        actor_id=current_user.id, target_type="user", target_id=user.id,
        action="delete", before={"role_ids": sorted(role.id for role in user.roles)}, after={},
    ))
    await db.delete(user)
    await _commit_user_changes(db)
    return SuccessResponse()


# 禁用/启用某个用户
@router.put(
    "/{user_id}/disable",
    summary="禁用某个用户",
    description="禁用某个用户",
    response_model=SuccessResponse,
)
async def disable_user(
    user_id: int,
    role_request: DisableUserRequest = Body(),
    current_user: User = Depends(require_permission(USER_PATH, USER_PUT_API)),
    db: AsyncSession = Depends(database.get_db),
):
    # 查询目标用户是否存在
    result = await db.execute(
        select(User).where(User.id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
        .options(selectinload(User.roles).selectinload(Role.permissions))
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="User not found"
        )
    await _get_roles_by_ids([cast(int, role.id) for role in user.roles], db, for_update=True)
    is_active = role_request.is_active
    _ensure_editable(user, current_user, is_active)
    if bool(user.is_active) != is_active:
        db.add(AuthorizationAudit(
            actor_id=current_user.id, target_type="user", target_id=user.id,
            action="set-active", before={"is_active": bool(user.is_active)},
            after={"is_active": is_active},
        ))
    await db.execute(update(User).where(User.id == user_id).values(is_active=is_active))
    await _commit_user_changes(db)
    return SuccessResponse()


async def _get_user_for_role_update(
    user_id: int, current_user: User, db: AsyncSession
) -> User:
    """锁定用户并重新加载关联，统一约束所有用户角色写入入口。"""
    result = await db.execute(
        select(User)
        .where(User.id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
        .options(selectinload(User.roles).selectinload(Role.permissions))
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")
    if not _user_info(user).is_editable:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="该用户的角色不可修改"
        )
    if user.id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="不能修改当前登录账号的角色",
        )
    return user


async def _get_roles_by_ids(
    role_ids: List[int], db: AsyncSession, *, for_update: bool = False
) -> List[Role]:
    """先验证完整角色集合存在，再返回去重后的角色对象。"""
    query = (
        select(Role).where(Role.id.in_(role_ids)).order_by(Role.id)
        .options(selectinload(Role.permissions))
    )
    if for_update:
        query = query.with_for_update().execution_options(populate_existing=True)
    result = await db.execute(query)
    roles = list(result.scalars().all())
    missing_ids = set(role_ids) - {cast(int, role.id) for role in roles}
    if missing_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"角色不存在：{sorted(missing_ids)}",
        )
    return roles


async def _save_user_roles(
    user: User, roles: List[Role], db: AsyncSession, current_user: User
) -> None:
    """验证基础角色和启用状态，在单个事务中保存完整用户角色集合。"""
    current_ids = {cast(int, role.id) for role in user.roles}
    selected_ids = {cast(int, role.id) for role in roles}
    # 按 ID 锁定涉及的角色并刷新权限，与角色授权及状态修改串行校验。
    await _get_roles_by_ids(sorted(current_ids | selected_ids), db, for_update=True)
    # 兼容两种普通角色命名，已关联的基础角色必须保留。
    fixed_ids = {
        cast(int, role.id) for role in user.roles if role.name in ("user", "normal")
    }
    if not fixed_ids.issubset(selected_ids):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="不能移除用户的基础角色"
        )
    if any(not role.is_active and role.id not in current_ids for role in roles):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="不能关联已停用的角色"
        )
    changed_ids = current_ids ^ selected_ids
    changed_roles = {role.id: role for role in [*user.roles, *roles] if role.id in changed_ids}
    ensure_grantable(current_user, [p for role in changed_roles.values() for p in role.permissions])
    if current_ids != selected_ids:
        # 只更改关联表，角色实体及其权限保持不变。
        user.roles = roles
        user.updated_at = func.now()  # type: ignore
        db.add(user)
        db.add(AuthorizationAudit(
            actor_id=current_user.id, target_type="user", target_id=user.id,
            action="assign-role", before=sorted(current_ids), after=sorted(selected_ids),
        ))
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="用户角色关联发生冲突，请刷新后重试",
        ) from exc


@router.put(
    "/{user_id}/roles",
    summary="设置用户角色",
    description="在单个事务中用完整角色 ID 列表替换关联，保留已关联的基础角色。",
    response_model=UserDetailResponse,
)
async def update_user_roles(
    user_id: int,
    role_request: UserRolesRequest,
    current_user: User = Depends(require_permission(USER_PATH, USER_ASSIGN_ROLE_API)),
    db: AsyncSession = Depends(database.get_db),
) -> UserDetailResponse:
    """整体设置用户角色，保存失败时不留下部分添加或移除的关联。

    Args:
        user_id: 待设置角色的用户 ID。
        role_request: 保存后应关联的完整角色 ID 集合。
        current_user: 已通过用户角色分配权限校验的操作者。
        db: 当前请求的异步数据库会话。

    Returns:
        用户详情及保存后的最新角色集合。
    """
    user = await _get_user_for_role_update(user_id, current_user, db)
    roles = await _get_roles_by_ids(role_request.role_ids, db)
    await _save_user_roles(user, roles, db, current_user)
    return _user_detail(await _get_user_or_404(user_id, db))


# 为用户追加角色，保留已有接口的追加语义。
@router.post(
    "/{user_id}/assign-roles",
    summary="为用户分配多个角色（通过ID）",
    description="根据角色ID列表为特定用户分配角色权限。",
    response_model=SuccessResponse,
)
async def assign_role_to_user(
    user_id: int,
    role_request: AssignRolesRequest = Body(default=AssignRolesRequest(role_ids=[])),
    current_user: User = Depends(require_permission(USER_PATH, USER_ASSIGN_ROLE_API)),
    db: AsyncSession = Depends(database.get_db),
) -> SuccessResponse:
    """追加角色，重复提交不会产生重复关联，账号保护与整体设置一致。"""
    user = await _get_user_for_role_update(user_id, current_user, db)
    roles_to_assign = await _get_roles_by_ids(role_request.role_ids, db)
    existing_role_ids = {role.id for role in user.roles}
    roles = list(user.roles) + [
        role for role in roles_to_assign if role.id not in existing_role_ids
    ]
    await _save_user_roles(user, roles, db, current_user)
    return SuccessResponse()


# 根据用户id获取用户的角色
@router.get(
    "/{user_id}/roles",
    summary="获取某个用户的角色",
    description="获取某个用户的角色列表（需要登录）",
    response_model=List[UserRoleResponse],
)
async def get_user_roles(
    user_id: int,
    current_user: User = Depends(require_permission(USER_PATH, USER_GET_API)),
    db: AsyncSession = Depends(database.get_db),
):
    # 获取用户信息
    result = await db.execute(
        select(User).where(User.id == user_id).options(selectinload(User.roles))
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="User not found"
        )
    return [
        UserRoleResponse(
            id=cast(int, role.id), name=str(role.name), display_name=role.display_name
        )
        for role in user.roles
    ]


# 批量删除某个用户的角色
@router.post(
    "/{user_id}/remove-roles",
    summary="批量删除某个用户的角色",
    description="批量删除某个用户的角色（需要登录）",
    response_model=SuccessResponse,
)
async def delete_user_roles(
    user_id: int,
    role_request: RemoveRolesRequest = Body(default=RemoveRolesRequest(role_ids=[])),
    current_user: User = Depends(require_permission(USER_PATH, USER_ASSIGN_ROLE_API)),
    db: AsyncSession = Depends(database.get_db),
) -> SuccessResponse:
    """移除指定角色，基础角色保护及账号保护与整体设置一致。"""
    user = await _get_user_for_role_update(user_id, current_user, db)
    role_ids = set(role_request.role_ids)
    existing_role_ids = {r.id for r in user.roles}
    missing_role_ids = role_ids - existing_role_ids
    if missing_role_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"User does not have roles: {list(missing_role_ids)}",
        )

    roles = [role for role in user.roles if role.id not in role_ids]
    await _save_user_roles(user, roles, db, current_user)
    return SuccessResponse()


@router.get("/{user_id}/role-options", response_model=UserRoleOptionsResponse)
async def get_user_role_options(
    user_id: int,
    current_user: User = Depends(require_any_permission(USER_GET_API, USER_ASSIGN_ROLE_API)),
    db: AsyncSession = Depends(database.get_db),
) -> UserRoleOptionsResponse:
    """返回已关联角色及操作者可授予的角色，不要求额外角色查询权限。"""
    user = await _get_user_or_404(user_id, db)
    result = await db.execute(select(Role).options(selectinload(Role.permissions)).order_by(Role.id))
    roles = list(result.scalars().all())
    names = permission_names(current_user)
    grantable_ids = {
        role.id for role in roles
        if all(can_grant(names, p.name) for p in role.permissions)
    }
    assigned_ids = {role.id for role in user.roles}
    can_assign = can_grant(names, USER_ASSIGN_ROLE_API)
    visible = [role for role in roles if role.id in assigned_ids or (can_assign and role.id in grantable_ids)]
    return UserRoleOptionsResponse(
        user=_user_detail(user), roles=await role_responses(db, visible),
        grantable_role_ids=sorted(grantable_ids) if can_assign else [],
    )


# 获取用户列表
@router.get(
    "/list",
    summary="获取用户列表",
    description="获取用户列表（需要登录，支持分页）",
    response_model=UserListResponse,
)
async def read_users(
    current_user: User = Depends(require_permission(USER_PATH, USER_GET_API)),
    db: AsyncSession = Depends(database.get_db),
    page: int = Query(1, ge=1, description="页码，从1开始"),
    page_size: int = Query(
        settings.DEFAULT_PAGE_SIZE,
        ge=1,
        le=settings.MAX_PAGE_SIZE,
        description="每页数量，最大100",
    ),
    search: UserSearchParams = Depends(),
) -> UserListResponse:
    """按用户 ID 升序获取用户列表，支持组合搜索和分页。

    通过权限依赖验证登录状态和用户查询权限。

    Args:
        current_user: 已通过用户查询权限验证的当前用户。
        db: 当前请求的异步数据库会话。
        page: 页码，从 1 开始，默认值为 1。
        page_size: 每页数量，默认值和上限由分页配置决定。
        search: 姓名、邮箱、关键词及启用状态，多条件按 AND 组合。

    Returns:
        用户基本信息列表、用户总数、当前页码、每页数量和总页数。
    """
    # 计算偏移量
    offset = (page - 1) * page_size

    filters = build_search_filters(User, search, ("name", "email", "description"))
    count_result = await db.execute(
        select(func.count()).select_from(User).where(*filters)
    )
    total = count_result.scalar_one()

    # 按唯一 ID 固定查询顺序，确保分页结果稳定。
    # 预加载 roles，避免序列化时触发懒加载（async 环境中会触发 greenlet 错误）
    result = await db.execute(
        select(User)
        .where(*filters)
        .options(selectinload(User.roles))
        .order_by(User.id.asc())
        .offset(offset)
        .limit(page_size)
    )
    users = result.scalars().all()

    # 计算总页数
    total_pages = (total + page_size - 1) // page_size

    # 将 User 模型转换为用户列表展示项。
    user_responses = [_user_info(user) for user in users]

    return UserListResponse(
        items=user_responses,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


# 根据用户id获取用户的权限
@router.get(
    "/{user_id}/permissions",
    summary="获取某个用户的权限",
    description="获取某个用户的权限列表（需要登录）",
    response_model=List[PermissionResponse],
)
async def get_user_permissions(
    user_id: int,
    current_user: User = Depends(require_permission(USER_PATH, USER_GET_API)),
    db: AsyncSession = Depends(database.get_db),
):
    # 获取用户信息
    result = await db.execute(
        select(User)
        .where(User.id == user_id)
        .options(selectinload(User.roles).selectinload(Role.permissions))
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="User not found"
        )
    return [
        PermissionResponse(
            id=cast(int, perm.id),
            name=str(perm.name),
            display_name=perm.display_name,
            description=perm.description,
        )
        for perm in user.permissions
    ]


# 放在 /list 等静态查询路由之后，避免将静态路径匹配为用户 ID。
@router.get(
    "/{user_id}",
    summary="获取用户详情",
    description="获取用户基本信息及角色，需要用户查询权限。",
    response_model=UserDetailResponse,
)
async def read_user(
    user_id: int,
    current_user: User = Depends(require_permission(USER_PATH, USER_GET_API)),
    db: AsyncSession = Depends(database.get_db),
) -> UserDetailResponse:
    """根据用户 ID 获取详情，不返回密码及密码哈希。"""
    return _user_detail(await _get_user_or_404(user_id, db))
