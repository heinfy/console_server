from pydantic import BaseModel
from typing import Optional, Generic, TypeVar, List

T = TypeVar("T")


class PermissionCreate(BaseModel):
    name: str
    display_name: str
    description: Optional[str] = None


class PermissionResponse(BaseModel):
    name: str
    display_name: str
    description: Optional[str] = None

    class Config:
        from_attributes = True


class AssignPermissionsRequest(BaseModel):
    permission_ids: list[int]
