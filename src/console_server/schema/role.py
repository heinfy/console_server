from datetime import datetime
from typing import Optional, List

from pydantic import BaseModel, ConfigDict, Field

from .common import PaginatedResponse
from .permission import PermissionInfoResponse


class RoleCreate(BaseModel):
    """创建角色的请求模型，操作保护标记由服务端设置。"""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    name: str = Field(min_length=1, max_length=64, description="唯一角色标识")
    display_name: str = Field(min_length=1, max_length=128, description="角色显示名称")
    description: Optional[str] = Field(default=None, description="角色描述")
    is_active: bool = Field(default=True, description="是否启用角色")


class RoleResponse(BaseModel):
    """角色详情及列表项，包含操作保护标记和审计时间。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    display_name: str
    description: Optional[str] = None
    is_active: bool
    is_deletable: bool = Field(description="是否允许删除角色")
    is_editable: bool = Field(description="是否允许编辑角色")
    user_count: int = Field(default=0, ge=0, description="关联用户数量，非零时禁止删除")
    created_at: datetime = Field(description="角色创建时间")
    updated_at: datetime = Field(description="角色最后更新时间")


class UserRoleResponse(BaseModel):
    id: int
    name: str
    display_name: str


class RoleListResponse(PaginatedResponse[RoleResponse]):
    """角色列表分页响应"""

    pass


class RoleUpdateRequest(BaseModel):
    """更新角色的请求模型；角色标识创建后不可修改。"""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    display_name: str = Field(min_length=1, max_length=128, description="角色显示名称")
    description: Optional[str] = Field(default=None, description="角色描述")
    is_active: bool = Field(default=True, description="是否启用角色")


class RolePermissionResponse(BaseModel):
    """角色当前关联的权限 ID，供权限设置页面回显选择。"""

    role_id: int
    permission_ids: List[int] = Field(default_factory=list, description="已关联的权限 ID")


class RolePermissionOptionsResponse(BaseModel):
    role: RoleResponse
    permissions: List[PermissionInfoResponse]
    permission_ids: List[int]
    grantable_permission_ids: List[int]
    editable: bool


class AssignRolesRequest(BaseModel):
    role_ids: list[int]


class RemoveRolesRequest(BaseModel):
    role_ids: list[int]
