from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi import APIRouter, Depends, HTTPException, Query, status, Body
from sqlalchemy.orm import selectinload
from typing import cast

from console_server.core.constants import (
    PERMISSION_PUT_API,
    ROLE_DELETE_API,
    ROLE_GET_API,
    ROLE_PATH,
    ROLE_POST_API,
)
from console_server.db import database
from console_server.model.rbac import User, Role, Permission
from console_server.schema.common import SuccessResponse
from console_server.schema.role import (
    RoleCreate,
    RoleListResponse,
    RoleResponse,
    RolePermissionResponse,
    RoleUpdateResponse,
)
from console_server.schema.permission import AssignPermissionsRequest
from console_server.core.config import settings


from console_server.utils.auth import require_permission


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
):
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
    )
    db.add(new_role)
    await db.commit()
    await db.refresh(new_role)
    return SuccessResponse()


# 给角色分配权限
@router.post(
    "/{role_id}/assign-permissions",
    summary="为角色分配多个权限（通过ID）",
    description="根据权限ID列表为特定角色分配权限。",
    response_model=SuccessResponse,
)
async def assign_permissions_to_role(
    role_id: int,
    permission_request: AssignPermissionsRequest,
    current_user: User = Depends(require_permission(ROLE_PATH, ROLE_POST_API)),
    db: AsyncSession = Depends(database.get_db),
):
    # 查询目标角色是否存在
    result = await db.execute(
        select(Role).where(Role.id == role_id).options(selectinload(Role.permissions))
    )
    role = result.scalar_one_or_none()
    if not role:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Role not found"
        )

    # 查询所有待分配的权限对象
    stmt = select(Permission).where(
        Permission.id.in_(permission_request.permission_ids)
    )
    result = await db.execute(stmt)
    permissions_to_assign = result.scalars().all()

    # 如果部分权限不存在，则抛出错误提示具体缺失项
    found_permission_ids = {cast(int, p.id) for p in permissions_to_assign}
    missing_permission_ids = (
        set(permission_request.permission_ids) - found_permission_ids
    )
    if missing_permission_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Permissions with IDs {list(missing_permission_ids)} not found",
        )

    # 过滤掉已存在的权限关系，防止重复添加
    existing_permission_ids = {cast(int, p.id) for p in role.permissions}
    new_permissions = [
        p
        for p in permissions_to_assign
        if cast(int, p.id) not in existing_permission_ids
    ]

    role.permissions.extend(new_permissions)
    db.add(role)
    await db.commit()
    await db.refresh(role)

    print(role.id)
    print(new_permissions)
    # 返回角色信息
    return SuccessResponse()


# 获取角色列表，需要分页
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
):
    offset = (page - 1) * page_size
    # 获取所有角色
    count_result = await db.execute(select(func.count()).select_from(Role))
    total = count_result.scalar_one()
    # 获取分页数据
    result = await db.execute(select(Role).offset(offset).limit(page_size))
    roles = result.scalars().all()

    # 计算总页数
    total_pages = (total + page_size - 1) // page_size

    return RoleListResponse(
        items=[
            RoleResponse(
                id=cast(int, role.id),
                name=cast(str, role.name),
                display_name=cast(str, role.display_name),
                description=cast(str, role.description),
                is_active=cast(bool, role.is_active),
            )
            for role in roles
        ],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


# 移除角色
@router.delete(
    "/{role_id}/remove",
    summary="移除角色",
    description="移除某个角色",
    response_model=SuccessResponse,
)
async def remove_role(
    role_id: int,
    current_user: User = Depends(require_permission(ROLE_PATH, ROLE_DELETE_API)),
    db: AsyncSession = Depends(database.get_db),
):
    result = await db.execute(select(Role).where(Role.id == role_id))
    role = result.scalar_one_or_none()
    if not role:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Role not found"
        )
    await db.delete(role)
    await db.commit()
    return SuccessResponse()


# 更新角色
@router.put(
    "/{role_id}/update",
    summary="更新角色",
    description="更新某个角色",
    response_model=SuccessResponse,
)
async def update_role(
    role_id: int,
    role_update: RoleUpdateResponse,
    current_user: User = Depends(require_permission(ROLE_PATH, PERMISSION_PUT_API)),
    db: AsyncSession = Depends(database.get_db),
):
    result = await db.execute(select(Role).where(Role.id == role_id))
    role = result.scalar_one_or_none()
    if not role:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Role not found"
        )

    role.display_name = role_update.display_name  # type: ignore
    role.description = role_update.description  # type: ignore
    role.is_active = role_update.is_active  # type: ignore

    db.add(role)
    await db.commit()
    await db.refresh(role)
    return SuccessResponse()


# 根据角色ID获取权限列表
@router.get(
    "/{role_id}/permissions",
    summary="获取角色的权限列表",
    description="根据角色ID获取该角色分配的权限列表",
    response_model=RolePermissionResponse,
)
async def get_role_permissions(
    role_id: int,
    current_user: User = Depends(require_permission(ROLE_PATH, ROLE_GET_API)),
    db: AsyncSession = Depends(database.get_db),
):
    result = await db.execute(
        select(Role).where(Role.id == role_id).options(selectinload(Role.permissions))
    )
    role = result.scalar_one_or_none()
    if not role:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Role not found"
        )
    return {
        "role_id": role.id,
        "permission_ids": [p.id for p in role.permissions],
    }
