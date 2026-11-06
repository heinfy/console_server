"""列表搜索参数，空字符串视为未筛选，布尔值允许明确选择 False。"""

from pydantic import BaseModel, ConfigDict, Field


class SearchParams(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    keyword: str | None = Field(default=None, max_length=256, description="名称或描述关键词")
    name: str | None = Field(default=None, max_length=128, description="名称或编码，模糊匹配")


class UserSearchParams(SearchParams):
    email: str | None = Field(default=None, max_length=256, description="邮箱，模糊匹配")
    is_active: bool | None = Field(default=None, description="是否启用")


class RoleSearchParams(SearchParams):
    display_name: str | None = Field(default=None, max_length=128, description="显示名称，模糊匹配")
    is_active: bool | None = Field(default=None, description="是否启用")
    is_editable: bool | None = Field(default=None, description="是否可编辑")
    is_deletable: bool | None = Field(default=None, description="是否可删除")


class PermissionSearchParams(SearchParams):
    display_name: str | None = Field(default=None, max_length=256, description="显示名称，模糊匹配")
    is_editable: bool | None = Field(default=None, description="是否可编辑")
    is_deletable: bool | None = Field(default=None, description="是否可删除")
