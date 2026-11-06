"""唯一的权限解释规则；旧 HTTP 方法只映射到资料维护能力。"""

import re
from collections.abc import Iterable

METHOD_ACTIONS = {"get": "read", "post": "create", "put": "update", "delete": "delete"}
MODULE_ACTIONS = {
    "chat": ("use",),
    "user": ("read", "create", "update", "delete", "assign-role"),
    "role": ("read", "create", "update", "delete", "assign-permission"),
    "permission": ("read", "create", "update", "delete"),
}
MENU_READ_PERMISSIONS = {
    "menu:chat": "api:chat:use",
    "menu:user": "api:user:read",
    "menu:role": "api:role:read",
    "menu:permission": "api:permission:read",
}
PERMISSION_CODE_PATTERN = re.compile(
    r"^(?:api:\*|api:[a-z][a-z0-9_-]*:(?:\*|[a-z][a-z0-9_-]*(?:,[a-z][a-z0-9_-]*)*)"
    r"|(?:menu|page|btn):(?:\*|[a-z][a-z0-9_/-]*(?::(?:\*|[a-z][a-z0-9_-]*))?))$"
)


def normalize_permission(name: str) -> str:
    if not name.startswith("api:") or name == "api:*":
        return name
    parts = name.split(":", 2)
    if len(parts) != 3:
        return name
    actions = {METHOD_ACTIONS.get(value.strip().lower(), value.strip().lower())
               for value in parts[2].split(",")}
    return f"api:{parts[1]}:{','.join(sorted(actions))}"


def expand_permissions(names: Iterable[str]) -> set[str]:
    """展开模块通配及旧方法编码；模块通配不包含授权操作。"""
    expanded: set[str] = set()
    for name in names:
        name = normalize_permission(name)
        if name == "api:*":
            expanded.add(name)
            for module, actions in MODULE_ACTIONS.items():
                expanded.update(f"api:{module}:{action}" for action in actions)
        elif name.startswith("api:") and len(name.split(":")) == 3:
            _, module, actions = name.split(":", 2)
            if actions == "*":
                expanded.add(name)
                expanded.update(f"api:{module}:{action}" for action in
                                ("read", "create", "update", "delete"))
            else:
                expanded.update(f"api:{module}:{action}" for action in actions.split(","))
        else:
            expanded.add(name)
    return expanded


def has_permission(names: Iterable[str], required: str) -> bool:
    required = normalize_permission(required)
    available = expand_permissions(names)
    if required in available:
        return True
    if required.startswith("api:"):
        if "api:*" in available:
            return True
        parts = required.split(":", 2)
        # 未登记模块的通配也仅覆盖维护动作，不覆盖新的授权动作。
        return (len(parts) == 3 and parts[2] in METHOD_ACTIONS.values()
                and f"api:{parts[1]}:*" in available)
    parts = required.split(":")
    return (f"{parts[0]}:*" in available or
            (len(parts) == 3 and f"{parts[0]}:{parts[1]}:*" in available))


def can_grant(names: Iterable[str], permission: str) -> bool:
    """所有权限项均不得超过操作者；全局权限只能由全局管理员授予。"""
    available = list(names)
    if "api:*" in available:
        return True
    normalized = normalize_permission(permission)
    if normalized == "api:*":
        return False
    return all(has_permission(available, MENU_READ_PERMISSIONS.get(item, item))
               for item in expand_permissions([normalized]))


def effective_capabilities(names: Iterable[str]) -> list[str]:
    names = list(names)
    expanded = expand_permissions(names)
    if "menu:*" in expanded or "api:*" in expanded:
        expanded.update(("menu:dashboard", "menu:chart", "menu:setting"))
    # 管理菜单必须逐项依据读取能力生成，避免菜单通配绕过页面权限。
    expanded.discard("menu:*")
    # 管理菜单与页面读取能力一致，不需要维护另一份管理菜单授权。
    for menu, permission in MENU_READ_PERMISSIONS.items():
        if has_permission(names, permission):
            expanded.add(menu)
        else:
            expanded.discard(menu)
    return sorted(expanded)
