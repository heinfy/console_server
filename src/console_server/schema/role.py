from pydantic import BaseModel
from typing import Optional, TypeVar, List

T = TypeVar("T")


class RoleCreate(BaseModel):
    name: str
    display_name: str
    description: Optional[str] = None
    is_active: Optional[bool] = True


class RoleResponse(BaseModel):
    name: str
    display_name: str
    description: Optional[str] = None
    is_active: Optional[bool] = True

    class Config:
        from_attributes = True


class RolePermissionResponse(BaseModel):
    role_id: int
    permission_ids: List[int] = []


class AssignRolesRequest(BaseModel):
    role_ids: list[int]


class RemoveRolesRequest(BaseModel):
    role_ids: list[int]
