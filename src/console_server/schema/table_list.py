from datetime import date
from typing import Literal

from pydantic import BaseModel

from .common import PaginatedResponse


class TableListItem(BaseModel):
    key: int
    name: str
    gender: Literal["男", "女"]
    age: int
    createAt: date
    address: str
    tags: list[str]


class TableListResponse(PaginatedResponse[TableListItem]):
    """示例列表的分页响应。"""
