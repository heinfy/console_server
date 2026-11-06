from datetime import datetime
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator
from typing import Annotated, List, Optional

from .role import UserRoleResponse, RoleResponse
from .permission import PermissionResponse
from .common import PaginatedResponse


class UserCreate(BaseModel):
    name: str
    email: EmailStr
    password: str


class CurrentUserResponse(BaseModel):
    id: int
    name: str
    email: str
    description: Optional[str] = None
    is_active: bool
    is_deletable: bool
    is_editable: bool
    roles: List[UserRoleResponse]
    permissions: List[PermissionResponse]
    capabilities: List[str] = Field(default_factory=list, description="后端计算的有效操作能力")

    class Config:
        from_attributes = True


class UserResponse(BaseModel):
    id: int
    name: str
    email: str
    roles: List[str]

    class Config:
        from_attributes = True


class Token(BaseModel):
    access_token: str
    token_type: str


class UserLogin(BaseModel):
    email: EmailStr
    password: str
    remember_me: bool = Field(default=False, alias="rememberMe")


class UserInfoResponse(BaseModel):
    """用户管理列表展示项，不包含密码及密码哈希。"""

    id: int
    name: str
    email: str
    description: Optional[str] = None
    is_active: bool
    is_deletable: bool
    is_editable: bool


class UserDetailResponse(UserInfoResponse):
    """用户详情，包含角色及创建、更新时间。"""

    roles: List[UserRoleResponse]
    created_at: datetime
    updated_at: datetime


class UserRoleOptionsResponse(BaseModel):
    user: UserDetailResponse
    roles: List[RoleResponse]
    grantable_role_ids: List[int]


class UserWriteRequest(BaseModel):
    """用户管理写入字段，不允许客户端设置账号保护标记。"""

    name: str = Field(min_length=1, max_length=100)
    email: EmailStr = Field(max_length=100)
    description: Optional[str] = Field(default=None, max_length=128)
    is_active: bool = True

    @field_validator("name", mode="before")
    @classmethod
    def strip_name(cls, value):
        """去除姓名首尾空白，再由字段长度约束验证。"""
        return value.strip() if isinstance(value, str) else value

    @field_validator("password", check_fields=False)
    @classmethod
    def validate_password_bytes(cls, value: Optional[str]) -> Optional[str]:
        """bcrypt 密码最多为 72 字节，避免多字节密码被静默截断。"""
        if value is not None and len(value.encode("utf-8")) > 72:
            raise ValueError("密码的 UTF-8 编码长度不能超过 72 字节")
        return value


class UserCreateRequest(UserWriteRequest):
    """管理端新增用户请求，必须提供初始密码。"""

    password: str = Field(min_length=6, max_length=72)


class UserUpdateRequest(UserWriteRequest):
    """管理端编辑用户请求，不提供密码时保留原密码。"""

    password: Optional[str] = Field(default=None, min_length=6, max_length=72)


class UserRolesRequest(BaseModel):
    """用户角色设置请求，提交保存后应关联的完整角色 ID 集合。"""

    model_config = ConfigDict(extra="forbid")

    role_ids: List[Annotated[int, Field(gt=0, strict=True)]] = Field(description="完整角色 ID 列表，角色 ID 必须为正整数")

    @field_validator("role_ids")
    @classmethod
    def validate_role_ids(cls, value: List[int]) -> List[int]:
        """拒绝非法 ID 并去重，防止重复生成多对多关联。"""
        if any(role_id <= 0 for role_id in value):
            raise ValueError("角色 ID 必须为正整数")
        return list(dict.fromkeys(value))


class UserListResponse(PaginatedResponse[UserInfoResponse]):
    """用户列表分页响应"""

    pass


class UpdateUserRequest(BaseModel):
    name: Optional[str]
    description: Optional[str] = None


class DisableUserRequest(BaseModel):
    is_active: bool
