# Console Server

FastAPI 控制台服务器项目。

## 功能特性

- FastAPI 框架
- PostgreSQL 异步数据库
- SQLAlchemy ORM
- 用户管理 API

## 开发环境

## 本地启动

### 启动 postgresql 数据库

1. 使用 docker-compose 启动 postgresql 数据库

`docker compose up -d`

2.  使用 docker 启动 postgresql 数据库
```bash
# PostgreSQL 18+ Docker 镜像引入的一个重大变更（breaking change）。
# 它不再将数据直接存放在 /var/lib/postgresql/data，而是改用与 pg_ctlcluster（Debian/Ubuntu PostgreSQL 管理工具）兼容的目录结构：
docker run -d --name my-postgres-18 \
  -e POSTGRES_PASSWORD=123456 \
  -p 5432:5432 \
  -v ~/postgres-data:/var/lib/postgresql \
  postgres:latest
```

### 使用 uv

```bash
# 一键安装指定Python版本、创建虚拟环境及依赖包
uv sync

# 创建虚拟环境
uv venv .venv

# 激活虚拟环境
# Linux/MacOS
source ./.venv/bin/activate
# Windows
source ./.venv/Scripts/activate

# 启动
bash ./dev.sh
```

## 权限管理

### 默认用户

| name  | email             | description | is_active | is_deletable | is_editable |
| ----- | ----------------- | ----------- | --------- | ------------ | ----------- |
| Admin | admin@example.com | -           | Ture      | False        | False       |
| Danny | Danny@example.com | -           | Ture      | Ture         | Ture        |

> **admin@example.com** 是管理员账户不可禁用、不可删除、不可编辑
>
> **Danny@example.com** 是测试账户可禁用、可删除、可编辑

### 默认角色

| name             | display_name | description | is_active | is_deletable | is_editable |
| ---------------- | ------------ | ----------- | --------- | ------------ | ----------- |
| admin            | 超级管理员   | -           | Ture      | False        | False       |
| user             | 普通用户     | -           | Ture      | False        | False       |
| user_admin       | 用户管理员   | -           | Ture      | False        | False       |
| role_admin       | 角色管理员   | -           | Ture      | False        | False       |
| permission_admin | 权限管理员   | -           | Ture      | False        | False       |

> **admin** 是管理员角色不可禁用、不可删除、不可编辑
>
> **user** 管理员账户不可禁用、不可删除、不可编辑

### 默认权限

| name                | display_name                     | description | is_deletable | is_editable |
| ------------------- | -------------------------------- | ----------- | ------------ | ----------- |
| `api:*`             | 访问所有接口（admin）            | -           | False        | False       |
| `api:user:*`        | 访问用户接口（user_admin）       | -           | False        | False       |
| `api:role:*`        | 访问角色接口（role_admin）       | -           | False        | False       |
| `api:persmission:*` | 访问权限接口（permission_admin） | -           | False        | False       |
|                     |                                  |             |              |             |
|                     |                                  |             |              |             |
|                     |                                  |             |              |             |
| `page:*`            | 预览所有页面（admin）            | -           | False        | False       |
| `btn:*`             | 展示所有接口（admin）            | -           | False        | False       |



```bash
# 后端接口权限
# api:[path]:[method]
api:*
api:user:*
api:user:POST,GET,PUT


# 前端页面权限
# page:[router]:view
page:*
page:home:view

# 前端按钮权限
# btn:[router]:[type]
btn:*
btn:home:*
btn:home:create
btn:home:update
btn:home:delete
```

### 伪代码

```python
# 示例：在 /src/console_server/api/v1/endpoints/ 目录下的路由文件中

from fastapi import APIRouter, Depends, HTTPException
from console_server.utils.auth import get_current_user, require_permission
from console_server.model.rbac import User

router = APIRouter(prefix="/api/v1/some-endpoint", tags=["some-endpoint"])

# 不需要特殊权限的普通接口
@router.get("/public-info")
async def get_public_info(current_user: User = Depends(get_current_user)):
    # 任何登录用户都可以访问
    return {"message": "这是公开信息"}

# 需要特定权限的接口
@router.delete("/delete-user")
async def delete_user(user_id: int, current_user: User = Depends(require_permission(["user_delete"]))):
    # 只有拥有 "user_delete" 权限的用户才能访问
    return {"message": f"用户 {user_id} 已删除"}

# 需要多种权限中任意一种的接口
@router.post("/manage-permissions")
async def manage_permissions(
    current_user: User = Depends(require_permission(["permission_manage", "admin_access"]))
):
    # 拥有 "permission_manage" 或 "admin_access" 权限的用户可以访问
    return {"message": "权限管理操作成功"}

# 需要管理员权限的接口
@router.get("/admin-only")
async def admin_only(current_user: User = Depends(require_permission(["admin_access"]))):
    # 只有管理员可以访问
    return {"message": "管理员专区"}
```

## 配置

```toml
[tool.hatch.build.targets.wheel]
packages = ["src.console_server"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```