from typing import Callable, Awaitable
from fastapi import Depends, HTTPException, status

from console_server import auth
from console_server import models
from console_server.rbac.enforcer import enforce


def require_permission(
    obj: str, act: str
) -> Callable[[models.User], Awaitable[models.User]]:
    """返回一个 FastAPI 依赖，用于在端点上进行 Casbin 权限校验。

    参数:
        obj: 资源对象，例如 API 路径或逻辑资源键（如 "action:user:delete"）
        act: 动作，例如 HTTP 方法（GET/POST/…）或自定义动作（VIEW/CLICK）

    返回:
        一个异步依赖函数（Awaitable[User]），FastAPI 会在路由处理前执行它。
        使用 Awaitable 是因为依赖是 async def，契合 FastAPI 异步依赖机制。
    """

    async def _checker(
        current_user: models.User = Depends(auth.get_current_user),
    ) -> models.User:
        # 角色可能在类型检查中被视为 SQLAlchemy Column，显式转为 str 保证 enforce 的参数正确
        subject = str(getattr(current_user, "role", None) or "user")
        # 以 角色-资源-动作 做鉴权
        if not enforce(subject, obj, act):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权限")
        return current_user

    return _checker
