from pydantic import BaseModel
from typing import Optional, TypeVar, List

from .common import PaginatedResponse

T = TypeVar("T")


class RoleCreate(BaseModel):
    name: str
    display_name: str
    description: Optional[str] = None
    is_active: Optional[bool] = True


class RoleResponse(BaseModel):
    id: int
    name: str
    display_name: str
    description: Optional[str] = None
    is_active: Optional[bool] = True

    class Config:
        from_attributes = True


class UserRoleResponse(BaseModel):
    id: int
    name: str
    display_name: str


class RoleListResponse(PaginatedResponse[RoleResponse]):
    """角色列表分页响应"""

    pass


class RoleUpdateResponse(BaseModel):
    display_name: str
    description: Optional[str] = None
    is_active: Optional[bool] = True


class RolePermissionResponse(BaseModel):
    role_id: int
    permission_ids: List[int] = []


class AssignRolesRequest(BaseModel):
    role_ids: list[int]


class RemoveRolesRequest(BaseModel):
    role_ids: list[int]
