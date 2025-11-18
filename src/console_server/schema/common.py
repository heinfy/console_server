from pydantic import BaseModel
from typing import Optional, Generic, TypeVar, List

T = TypeVar("T")


class TokenData(BaseModel):
    email: Optional[str] = None


class PaginatedResponse(BaseModel, Generic[T]):
    """通用分页响应模型"""

    items: List[T]
    total: int
    page: int
    page_size: int
    total_pages: int
