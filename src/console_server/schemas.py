from pydantic import BaseModel, EmailStr
from typing import Optional, Generic, TypeVar, List

T = TypeVar("T")


class UserCreate(BaseModel):
    name: str
    email: EmailStr
    password: str


class UserResponse(BaseModel):
    id: int
    name: str
    email: str

    class Config:
        from_attributes = True  # 兼容 SQLAlchemy 模型


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class Token(BaseModel):
    access_token: str
    token_type: str


class TokenData(BaseModel):
    email: Optional[str] = None


class PaginatedResponse(BaseModel, Generic[T]):
    """通用分页响应模型"""

    items: List[T]
    total: int
    page: int
    page_size: int
    total_pages: int


class UserListResponse(PaginatedResponse[UserResponse]):
    """用户列表分页响应"""

    pass
