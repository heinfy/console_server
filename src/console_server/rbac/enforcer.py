"""Casbin Enforcer 初始化与统一鉴权入口。

本模块负责：
- 使用 SQLAlchemy Adapter（同步引擎）加载/持久化策略
- 首次启动且策略表为空时，从本地 policy.csv 导入默认策略
- 提供统一的 `enforce(sub, obj, act)` 供中间件与依赖调用

说明：
- Adapter 需要“同步”数据库驱动，因此会把类似 `postgresql+asyncpg://` 转换为
  `postgresql://`，并要求环境中安装同步驱动（如 psycopg2-binary）。
"""

import os
from functools import lru_cache
from typing import Any
import casbin  # type: ignore
import casbin_sqlalchemy_adapter  # type: ignore
from console_server.core.config import settings


@lru_cache
def get_enforcer() -> Any:
    """获取单例 Enforcer。

    - 使用本地 `model.conf` 作为模型定义
    - 使用 SQLAlchemy Adapter 连接数据库持久化策略
    - 当策略表为空时，尝试从 `policy.csv` 导入一次默认策略
    """
    base_dir = os.path.dirname(os.path.abspath(__file__))
    model_path = os.path.join(base_dir, "model.conf")

    # 将异步数据库 URL 转换为同步 URL 以供 Adapter 使用
    db_url = settings.DATABASE_URL
    if "+asyncpg" in db_url:
        db_url = db_url.replace("+asyncpg", "")
    if "+aiosqlite" in db_url:
        db_url = db_url.replace("+aiosqlite", "")

    # 使用同步引擎的 Adapter（需要同步驱动，如 psycopg2-binary）
    adapter = casbin_sqlalchemy_adapter.Adapter(db_url)
    e = casbin.Enforcer(model_path, adapter)

    # 如果策略表为空，尝试从 policy.csv 初始化一次策略
    try:
        if not e.get_policy() and not e.get_grouping_policy():
            policy_path = os.path.join(base_dir, "policy.csv")
            if os.path.exists(policy_path):
                with open(policy_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#"):
                            continue
                        parts = [s.strip() for s in line.split(",")]
                        if not parts:
                            continue
                        # p, <sub>, <obj>, <act>
                        if parts[0] == "p" and len(parts) >= 4:
                            _, sub, obj, act = parts[:4]
                            e.add_policy(sub, obj, act)
                        # g, <user_or_role>, <role>
                        elif parts[0] == "g" and len(parts) >= 3:
                            _, user_or_role, role = parts[:3]
                            e.add_grouping_policy(user_or_role, role)
                e.save_policy()
    except Exception:
        # 初始化失败不影响服务启动，策略可后续通过接口或脚本导入
        pass

    return e


def enforce(sub: str, obj: str, act: str) -> bool:
    """统一权限判定入口。

    参数:
        sub: 主体（如角色名、用户名或其它标识）
        obj: 资源（API 路径或逻辑资源键）
        act: 动作（HTTP 方法或自定义动作）

    返回:
        是否允许访问。
    """
    e = get_enforcer()
    return bool(e.enforce(sub, obj, act))
