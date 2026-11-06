"""由后端提供示例列表数据，在分页前执行组合筛选。"""

from datetime import date, timedelta
from random import Random
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from console_server.core.config import settings
from console_server.schema.table_list import TableListItem, TableListResponse
from console_server.utils.auth import get_current_user


router = APIRouter(prefix="/list", tags=["示例列表"], dependencies=[Depends(get_current_user)])


def _generate_mock_data() -> tuple[TableListItem, ...]:
    # 固定种子和日期，让翻页、查询及服务重启后得到一致的数据。
    random = Random(0)
    addresses = ("中国", "美国", "韩国", "日本", "德国")
    jobs = ("开发者", "老板", "设计师", "经理", "测试工程师")
    companies = ("microsoft", "apple", "google", "facebook", "amazon")
    return tuple(
        TableListItem(
            key=index,
            name=f"Test {index + 1}",
            gender=random.choice(("男", "女")),
            age=random.randint(0, 100),
            createAt=date(2023, 11, 6) - timedelta(days=random.randrange(100)),
            address=random.choice(addresses),
            tags=[random.choice(jobs), random.choice(companies)],
        )
        for index in range(100)
    )


_MOCK_DATA = _generate_mock_data()


@router.get("/table-list", response_model=TableListResponse, summary="查询示例列表 mock 数据")
async def get_table_list(
    page: int = Query(1, ge=1),
    page_size: int = Query(settings.DEFAULT_PAGE_SIZE, ge=1, le=settings.MAX_PAGE_SIZE),
    name: str | None = Query(None, max_length=128),
    gender: Literal["男", "女"] | None = None,
    age_min: int | None = Query(None, ge=0, le=100),
    age_max: int | None = Query(None, ge=0, le=100),
    address: Literal["中国", "美国", "韩国", "日本", "德国"] | None = None,
    created_at: date | None = None,
    created_from: date | None = None,
    created_to: date | None = None,
) -> TableListResponse:
    if age_min is not None and age_max is not None and age_min > age_max:
        raise HTTPException(status_code=422, detail="最小年龄不能大于最大年龄")
    if created_from is not None and created_to is not None and created_from > created_to:
        raise HTTPException(status_code=422, detail="开始日期不能晚于结束日期")

    keyword = (name or "").strip().casefold()
    items = [
        item for item in _MOCK_DATA
        if (not keyword or keyword in item.name.casefold())
        and (gender is None or item.gender == gender)
        and (age_min is None or item.age >= age_min)
        and (age_max is None or item.age <= age_max)
        and (address is None or item.address == address)
        and (created_at is None or item.createAt == created_at)
        and (created_from is None or item.createAt >= created_from)
        and (created_to is None or item.createAt <= created_to)
    ]
    total = len(items)
    offset = (page - 1) * page_size
    return TableListResponse(
        items=items[offset:offset + page_size],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=(total + page_size - 1) // page_size,
    )
