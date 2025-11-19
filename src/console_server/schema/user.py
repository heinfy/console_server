from pydantic import BaseModel, EmailStr
from typing import List, Optional
from .common import PaginatedResponse


class UserCreate(BaseModel):
    name: str
    email: EmailStr
    password: str


class CurrentUserResponse(BaseModel):
    id: int
    name: str
    email: str
    is_active: bool
    is_deletable: bool
    is_editable: bool
    roles: List[str]
    permissions: List[str]

    class Config:
        from_attributes = True  # 兼容 SQLAlchemy 模型


class UserResponse(BaseModel):
    id: int
    name: str
    email: str
    roles: List[str]

    class Config:
        from_attributes = True  # 兼容 SQLAlchemy 模型


class Token(BaseModel):
    access_token: str
    token_type: str


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class UserListResponse(PaginatedResponse[UserResponse]):
    """用户列表分页响应"""

    pass


class UpdateUserRequest(BaseModel):
    name: Optional[str]
    description: Optional[str]


class DisableUserRequest(BaseModel):
    is_active: bool
