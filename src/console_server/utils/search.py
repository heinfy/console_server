"""为列表和总数查询生成相同的搜索条件。"""

from sqlalchemy import or_
from sqlalchemy.sql.elements import ColumnElement

from console_server.model.rbac import Permission, Role, User
from console_server.schema.search import SearchParams


def build_search_filters(
    model: type[User] | type[Role] | type[Permission],
    search: SearchParams,
    keyword_fields: tuple[str, ...],
) -> list[ColumnElement[bool]]:
    filters = []
    for name, value in search.model_dump().items():
        if value is None or value == "":
            continue
        if name == "keyword":
            filters.append(or_(*(
                getattr(model, field).icontains(value, autoescape=True)
                for field in keyword_fields
            )))
        elif isinstance(value, bool):
            filters.append(getattr(model, name).is_(value))
        else:
            # 转义 LIKE 通配符，让 % 和 _ 按用户输入的字面文本匹配。
            filters.append(getattr(model, name).icontains(value, autoescape=True))
    return filters
