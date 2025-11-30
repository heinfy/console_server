from pydantic import BaseModel
from typing import Optional, Generic, TypeVar, List

T = TypeVar("T")


class PermissionCreate(BaseModel):
    name: str
    display_name: str
    description: Optional[str] = None
    is_deletable: bool = False
    is_editable: bool = False


class PermissionResponse(BaseModel):
    id: int
    name: str
    display_name: str

    class Config:
        from_attributes = True


class AssignPermissionsRequest(BaseModel):
    permission_ids: list[int]
