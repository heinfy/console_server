from datetime import datetime
from typing import Annotated, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator
from console_server.core.permissions import PERMISSION_CODE_PATTERN, normalize_permission


class PermissionCreate(BaseModel):
    """创建权限的请求模型，校验必填字段和数据库字段长度。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=128, description="唯一权限编码")
    display_name: str = Field(min_length=1, max_length=256, description="权限显示名称")
    description: Optional[str] = Field(default=None, description="权限描述")
    is_deletable: bool = Field(default=False, description="权限是否允许删除")
    is_editable: bool = Field(default=False, description="权限是否允许编辑")

    @field_validator("name")
    @classmethod
    def validate_code(cls, value: str) -> str:
        if not PERMISSION_CODE_PATTERN.fullmatch(normalize_permission(value)):
            raise ValueError("权限编码格式应为 api:模块:操作、menu:页面、page:页面:view 或 btn:页面:操作")
        return value


class PermissionUpdate(PermissionCreate):
    """更新权限的请求模型，未提交的可选字段保留原值。"""


class PermissionResponse(BaseModel):
    """权限基础信息响应模型。"""

    id: int
    name: str
    display_name: str

    class Config:
        from_attributes = True


class PermissionInfoResponse(PermissionResponse):
    """权限列表详情响应模型，包含描述、操作标记及审计时间。"""

    description: Optional[str] = Field(default=None, description="权限描述")
    is_deletable: bool = Field(description="权限是否允许删除")
    is_editable: bool = Field(description="权限是否允许编辑")
    role_count: int = Field(default=0, ge=0, description="引用该权限的角色数量，非零时禁止删除")
    created_at: datetime = Field(description="权限创建时间")
    updated_at: datetime = Field(description="权限最后更新时间")


class AssignPermissionsRequest(BaseModel):
    """角色权限关联请求；空列表用于清空关联，重复 ID 由服务端去重。"""

    model_config = ConfigDict(extra="forbid")

    permission_ids: list[Annotated[int, Field(gt=0, strict=True)]] = Field(
        description="需要关联的权限 ID 列表，每项必须为正整数"
    )
