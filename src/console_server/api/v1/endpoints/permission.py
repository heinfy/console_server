from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi import APIRouter, Depends, HTTPException, Query, status

from console_server.core.constants import (
    PERMISSION_PATH,
    PERMISSION_DELETE_API,
    PERMISSION_POST_API,
    PERMISSION_PUT_API,
    PERMISSION_GET_API,
    ROLE_GET_API,
    ROLE_ASSIGN_PERMISSION_API,
)
from console_server.db import database
from console_server.model.rbac import User, Role, Permission, AuthorizationAudit, role_permissions
from console_server.schema.common import PaginatedResponse, SuccessResponse
from console_server.core.config import settings
from console_server.schema.search import PermissionSearchParams
from console_server.utils.search import build_search_filters
from console_server.schema.permission import (
    PermissionCreate,
    PermissionInfoResponse,
    PermissionUpdate,
)
from console_server.utils.auth import require_permission, require_any_permission, ensure_grantable
from console_server.utils.associations import permission_responses, role_responses
from console_server.schema.role import RoleResponse

router = APIRouter(prefix=f"/{PERMISSION_PATH}", tags=[PERMISSION_PATH])


# 创建权限
@router.post(
    "/create",
    summary="创建权限",
    description="创建新权限并保存可编辑、可删除标记，权限编码不可重复",
    response_model=PermissionInfoResponse,
)
async def create_permission(
    permission: PermissionCreate,
    current_user: User = Depends(
        require_permission(PERMISSION_PATH, PERMISSION_POST_API)
    ),
    db: AsyncSession = Depends(database.get_db),
) -> PermissionInfoResponse:
    """创建权限，返回包含操作标记和审计时间的完整详情。

    Args:
        permission: 经过字段校验的权限创建参数。
        current_user: 拥有权限创建能力的当前用户。
        db: 由依赖注入提供的异步数据库会话。

    Returns:
        已持久化的权限详情。

    Raises:
        HTTPException: 权限编码重复时返回 400。
    """
    # 检查权限名称是否已存在
    result = await db.execute(
        select(Permission).where(Permission.name == permission.name)
    )
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Permission name already exists",
        )
    # 创建新权限
    new_permission = Permission(
        name=permission.name,
        display_name=permission.display_name,
        description=permission.description,
        is_deletable=permission.is_deletable,
        is_editable=permission.is_editable,
    )
    db.add(new_permission)
    try:
        await db.commit()
    except IntegrityError as exc:
        # 唯一约束处理并发创建同一编码的情况，回滚后允许继续使用会话。
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Permission name already exists",
        ) from exc
    await db.refresh(new_permission)
    return PermissionInfoResponse.model_validate(new_permission)


# 更新权限
@router.put(
    "/{permission_id}/update",
    summary="更新权限",
    description="更新允许编辑的权限元信息，编码保持原值，未提交的可选字段保持原值",
    response_model=SuccessResponse,
)
async def update_permission(
    permission_id: int,
    permission: PermissionUpdate,
    current_user: User = Depends(
        require_permission(PERMISSION_PATH, PERMISSION_PUT_API)
    ),
    db: AsyncSession = Depends(database.get_db),
) -> SuccessResponse:
    """更新权限元信息，同时检查记录保护标记及编码不可变约束。

    Args:
        permission_id: 待更新的权限ID。
        permission: 权限更新参数，未提交的可选字段不会被覆盖。
        current_user: 拥有权限更新能力的当前用户。
        db: 由依赖注入提供的异步数据库会话。

    Returns:
        通用成功响应。

    Raises:
        HTTPException: 记录不存在时返回 404，不可编辑时返回 403，编码变更时返回 400。
    """
    result = await db.execute(select(Permission).where(Permission.id == permission_id))
    existing_permission = result.scalar_one_or_none()
    if not existing_permission:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Permission not found"
        )
    if not existing_permission.is_editable:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Permission cannot be edited",
        )
    if permission.name != existing_permission.name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="权限编码创建后不可修改")
    # 排除当前记录，允许保留原编码，但不能使用其他权限的编码。
    result = await db.execute(
        select(Permission).where(
            Permission.name == permission.name,
            Permission.id != permission_id,
        )
    )
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Permission name already exists",
        )
    # 仅更新显式提交的字段，避免默认值将已有操作标记重置为 False。
    for field, value in permission.model_dump(exclude_unset=True).items():
        setattr(existing_permission, field, value)
    db.add(existing_permission)
    try:
        await db.commit()
    except IntegrityError as exc:
        # 捕获并发更新引起的编码冲突，并恢复数据库会话。
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Permission name already exists",
        ) from exc
    await db.refresh(existing_permission)
    return SuccessResponse()


# 移除权限
@router.delete(
    "/{permission_id}/remove",
    summary="移除权限",
    description="仅删除未被角色引用且允许删除的权限；有引用时必须先解除关联",
    response_model=SuccessResponse,
)
async def remove_permission(
    permission_id: int,
    current_user: User = Depends(
        require_permission(PERMISSION_PATH, PERMISSION_DELETE_API)
    ),
    db: AsyncSession = Depends(database.get_db),
) -> SuccessResponse:
    """删除未被引用的权限，禁止删除受保护或使用中的权限。

    Args:
        permission_id: 待删除的权限ID。
        current_user: 拥有权限删除能力的当前用户。
        db: 由依赖注入提供的异步数据库会话。

    Returns:
        通用成功响应。

    Raises:
        HTTPException: 不存在时返回 404，受保护或无权操作时返回 403，仍被引用时返回 409。
    """
    # 与角色授权操作锁定同一权限行；引用检查在获得锁后单独查询。
    result = await db.execute(
        select(Permission)
        .where(Permission.id == permission_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    existing_permission = result.scalar_one_or_none()
    if not existing_permission:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Permission not found"
        )
    if not existing_permission.is_deletable:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Permission cannot be deleted",
        )
    ensure_grantable(current_user, [existing_permission])
    result = await db.execute(
        select(func.count()).select_from(role_permissions)
        .where(role_permissions.c.permission_id == permission_id)
    )
    role_count = result.scalar_one()
    if role_count:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={
            "code": "permission_in_use",
            "message": f"该权限已关联 {role_count} 个角色，请先解除关联后再删除",
            "role_count": role_count,
        })
    db.add(AuthorizationAudit(
        actor_id=current_user.id, target_type="permission", target_id=existing_permission.id,
        action="delete", before={"name": existing_permission.name,
                                 "display_name": existing_permission.display_name,
                                 "role_ids": []}, after={},
    ))
    # 直接删除实体，避免 ORM 自动解除引用而绕过数据库的 RESTRICT 约束。
    try:
        await db.execute(delete(Permission).where(Permission.id == permission_id))
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={
            "code": "permission_in_use", "message": "权限引用已变化，请刷新并解除关联后重试",
        }) from exc
    return SuccessResponse()


# 获取权限列表
@router.get(
    "/list",
    summary="获取权限列表",
    description="获取所有权限的详情列表（需要登录，按权限ID升序排列）",
    response_model=list[PermissionInfoResponse],
)
async def list_permissions(
    current_user: User = Depends(require_permission(PERMISSION_PATH, PERMISSION_GET_API)),
    db: AsyncSession = Depends(database.get_db),
    search: PermissionSearchParams = Depends(),
) -> list[PermissionInfoResponse]:
    """获取权限详情列表，保留数组响应格式供列表展示和权限选择使用。

    Args:
        current_user: 通过登录认证的当前用户。
        db: 由依赖注入提供的异步数据库会话。

    Returns:
        按权限ID升序排列的权限详情列表，无数据时返回空列表。
    """
    # 固定排序，保证前端刷新和分页展示时的记录顺序一致。
    filters = build_search_filters(Permission, search, ("name", "display_name", "description"))
    result = await db.execute(select(Permission).where(*filters).order_by(Permission.id))
    permissions = result.scalars().all()
    return await permission_responses(db, permissions)


@router.get(
    "/page",
    summary="分页搜索权限",
    description="按关键词、编码、显示名称及编辑/删除标记组合搜索权限，按 ID 升序分页。",
    response_model=PaginatedResponse[PermissionInfoResponse],
)
async def page_permissions(
    current_user: User = Depends(require_permission(PERMISSION_PATH, PERMISSION_GET_API)),
    db: AsyncSession = Depends(database.get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(settings.DEFAULT_PAGE_SIZE, ge=1, le=settings.MAX_PAGE_SIZE),
    search: PermissionSearchParams = Depends(),
) -> PaginatedResponse[PermissionInfoResponse]:
    """管理页面使用分页响应；/list 保留权限目录查询的完整数组格式。"""
    filters = build_search_filters(Permission, search, ("name", "display_name", "description"))
    count_result = await db.execute(
        select(func.count()).select_from(Permission).where(*filters)
    )
    total = count_result.scalar_one()
    result = await db.execute(
        select(Permission).where(*filters).order_by(Permission.id)
        .offset((page - 1) * page_size).limit(page_size)
    )
    return PaginatedResponse[PermissionInfoResponse](
        items=await permission_responses(db, list(result.scalars().all())),
        total=total,
        page=page,
        page_size=page_size,
        total_pages=(total + page_size - 1) // page_size,
    )


# 获取单个权限详情
@router.get(
    "/{permission_id}",
    summary="获取权限详情",
    description="根据权限ID获取完整详情（需要登录）",
    response_model=PermissionInfoResponse,
)
async def get_permission(
    permission_id: int,
    current_user: User = Depends(require_permission(PERMISSION_PATH, PERMISSION_GET_API)),
    db: AsyncSession = Depends(database.get_db),
) -> PermissionInfoResponse:
    """读取权限详情，供前端查看和编辑前获取最新数据。

    Args:
        permission_id: 待查询的权限ID。
        current_user: 通过登录认证的当前用户。
        db: 由依赖注入提供的异步数据库会话。

    Returns:
        权限详情。

    Raises:
        HTTPException: 记录不存在时返回 404。
    """
    result = await db.execute(select(Permission).where(Permission.id == permission_id))
    permission = result.scalar_one_or_none()
    if permission is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Permission not found"
        )
    return (await permission_responses(db, [permission]))[0]


@router.get("/{permission_id}/roles", response_model=PaginatedResponse[RoleResponse])
async def get_permission_roles(
    permission_id: int,
    current_user: User = Depends(require_permission(PERMISSION_PATH, PERMISSION_GET_API)),
    role_reader: User = Depends(require_any_permission(ROLE_GET_API, ROLE_ASSIGN_PERMISSION_API)),
    db: AsyncSession = Depends(database.get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(settings.DEFAULT_PAGE_SIZE, ge=1, le=settings.MAX_PAGE_SIZE),
) -> PaginatedResponse[RoleResponse]:
    """分页查看引用角色；同时要求角色查询或授权能力，解除关联沿用角色设置接口。"""
    if (await db.execute(select(Permission.id).where(Permission.id == permission_id))).scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Permission not found")
    condition = role_permissions.c.permission_id == permission_id
    total = (await db.execute(select(func.count()).select_from(role_permissions).where(condition))).scalar_one()
    result = await db.execute(
        select(Role).join(role_permissions, Role.id == role_permissions.c.role_id)
        .where(condition).order_by(Role.id).offset((page - 1) * page_size).limit(page_size)
    )
    return PaginatedResponse[RoleResponse](
        items=await role_responses(db, list(result.scalars().all())), total=total,
        page=page, page_size=page_size, total_pages=(total + page_size - 1) // page_size,
    )
