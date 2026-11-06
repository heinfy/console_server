# Console Server

FastAPI 控制台服务器项目。

首次启动和多环境部署请参阅 [后端启动教程](docs/后端启动教程.md)，包含数据库初始化、密钥配置、预发 / 生产启动及常见问题。
预发和生产通过容器部署请参阅 [Docker 部署教程](docs/Docker部署教程.md)，使用相同镜像和各环境独立的运行配置。

## 功能特性

- FastAPI 框架
- PostgreSQL 异步数据库
- SQLAlchemy ORM
- 用户管理 API

`/list/table-list` 示例页面通过 `GET /api/v1/list/table-list` 读取后端生成的 100 条 mock 数据。
接口需要有效登录会话，使用通用分页响应 `items/total/page/page_size/total_pages`，
支持 `page`、`page_size`（1–100）、`name`（忽略大小写的模糊匹配）、`gender`、
`age_min`、`age_max`、`address`、`created_at`、`created_from` 和 `created_to`。
日期格式为 `YYYY-MM-DD`，年龄和日期范围包含边界，多条件按 AND 组合后再分页。
示例数据使用固定随机种子和日期，查询、刷新及后端重启后保持一致，无需新增数据库表。

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

# 启动开发环境
bash scripts/start.sh dev
```

### 多环境启动

开发、预发、生产和 Docker 共用 `scripts/start.sh`，启动前由 `environment.py` 加载对应配置文件。
开发环境传入 `dev`。从任意工作目录给出脚本路径都可以启动。

| 环境 | 启动命令 | 自动读取的配置 | 默认 DEBUG / 热更新 |
| --- | --- | --- | --- |
| 开发 | `bash scripts/start.sh dev` | `.env.dev`，不存在时读取 `.env` | 开启 / 开启 |
| 预发 | `bash scripts/start.sh pre` | `.env.pre` | 关闭 / 关闭 |
| 生产 | `bash scripts/start.sh prod` | `.env.prod` | 关闭 / 关闭 |
| 测试 / 自定义 | `bash scripts/start.sh test` / `bash scripts/start.sh uat` | `.env.test` / `.env.uat` | 关闭 / 关闭 |

`local`、`development` 等同于 `dev`；`staging`、`preprod` 等同于 `pre`；
`production` 等同于 `prod`，`testing` 等同于 `test`。别名读取规范环境名的文件。
应用内的 `ENV` 由启动入口设置，配置文件无需填写 `ENV`。

```bash
# 开发环境
cp .env.example .env.dev
bash scripts/start.sh dev

# 预发：填写 .env.pre 的数据库连接及 SECRET_KEY 后启动
cp .env.pre.example .env.pre
bash scripts/start.sh pre

# 生产：填写 .env.prod 的数据库连接及 SECRET_KEY 后启动
cp .env.prod.example .env.prod
bash scripts/start.sh prod

# 由部署平台选择环境；未传参数且未设置 ENV 时默认 prod
ENV=pre bash scripts/start.sh

# 指定配置文件；相对路径基于项目根目录，支持绝对路径
bash scripts/start.sh pre --env-file /etc/console/pre.env

# 覆盖端口并检查配置，不启动服务，也不打印数据库地址或密钥
PORT=8080 bash scripts/start.sh prod --dry-run

# Docker：自动选择同一环境文件，后续参数传给部署 Compose
bash scripts/start.sh pre --docker up -d --build
bash scripts/start.sh prod --docker up -d --build
```

配置优先级为：**系统环境变量 > 所选配置文件 > 启动默认值**。
环境名优先级为：**命令行参数 > 系统 ENV > prod**。
`--env-file` 优先于系统 `ENV_FILE`，然后才自动选择 `.env.<环境名>`。
只加载一个配置文件；预发、生产、测试和自定义环境不会自动读取开发用的 `.env`。
没有对应配置文件时可以完全使用部署平台注入的环境变量；显式指定的文件缺失会报错。
开发以外的环境必须显式提供 `DATABASE_URL` 和 `SECRET_KEY`，各环境使用独立的数据库和密钥。
实际 `.env` 文件已加入 Git 忽略规则，示例文件可以提交。

| 变量 | 默认值 / 作用 |
| --- | --- |
| `PORT` / `HOST` | `8000` / `0.0.0.0` |
| `DEBUG` | 开发为 `true`，其他环境为 `false`；控制调试及 API 文档 |
| `RELOAD` | 仅开发默认开启；其他环境必须关闭 |
| `WORKERS` | `1`；热更新开启时必须为 `1` |
| `LOG_LEVEL` | Uvicorn 日志等级，默认 `info`；应用日志继续用 `GLOBAL_LOG_LEVEL` 等配置 |
| `FORWARDED_ALLOW_IPS` | 默认 `127.0.0.1`，可填写反向代理的 IP / 网段 |
| `ENV_FILE` | 显式指定配置文件 |
| `PYTHON_BIN` | 显式指定 Python；否则依次使用已激活的虚拟环境、项目 `.venv`、系统 `python3` |

当前应用在每个进程中启动定时清理任务，因此默认保持单进程。
设置多个 `WORKERS` 或部署多个副本会各自执行定时任务；扩容前需要处理任务的重复执行。
脚本以前台方式运行并传递停止信号，进程重启由容器平台或 systemd 管理。
依赖在构建阶段通过 `uv sync --frozen --no-dev` 安装，启动阶段不安装依赖。
Dockerfile 已按锁文件安装完整运行依赖，并通过 `scripts/start.sh` 启动。
宿主机运行 `--docker` 入口也需要已安装的 Python 运行依赖。`PORT` 是唯一应用端口配置，
Compose 同时用它设置容器映射；`HOST_PORT` 只改变宿主机发布端口，默认等于 `PORT`。
`--env-file`、`--dry-run` 写在 `--docker` 前，后续参数传给 Compose；不带参数时默认 `up -d`。
脚本自动选择 `docker-compose.deploy.yaml` 并注入对应环境配置；默认的
`docker-compose.yaml` 继续用于本地 PostgreSQL。

## 权限管理

### 权限 CRUD

| 方法 | 路径 | 功能 |
| --- | --- | --- |
| GET | `/api/v1/permission/list` | 查询全部权限详情 |
| GET | `/api/v1/permission/page` | 组合搜索并分页查询权限 |
| GET | `/api/v1/permission/{permission_id}` | 查询单个权限详情 |
| POST | `/api/v1/permission/create` | 创建权限 |
| PUT | `/api/v1/permission/{permission_id}/update` | 更新权限 |
| DELETE | `/api/v1/permission/{permission_id}/remove` | 删除未被角色引用的权限 |
| GET | `/api/v1/permission/{permission_id}/roles` | 分页查看引用角色，额外需要角色查询或角色授权权限 |

所有接口需要登录。查询、新增、编辑和删除分别校验 `api:permission:read`、
`api:permission:create`、`api:permission:update` 和 `api:permission:delete`，兼容旧 HTTP 方法编码。
`api:permission:*` 覆盖这些维护操作，`api:*` 提供全局管理能力；角色名称本身不授予权限。
权限编码最多 128 个字符，显示名称最多 256 个字符，两者均不能为空。权限编码必须符合
`api:模块:操作` 或 `menu/page/btn` 规则，创建后不可修改；显示名称及描述可以修改。

创建请求支持 `description`、`is_editable` 和 `is_deletable`；直接调用接口时，
未提交的操作标记默认是 `false`。前端新增表单默认开启这两个标记。
更新只修改显式提交的字段；描述传入 `null` 表示清空。
不可编辑的权限不能更新，不可删除的权限不能删除，管理员也遵循记录保护标记。
权限列表及详情返回 `role_count`；即使角色已停用，其引用也计入数量并阻止权限删除。
有引用时返回 409，先通过角色权限设置取消关联并保存，再删除权限。

权限编码重复返回 400，无操作授权或记录受保护返回 403，记录不存在返回 404，
字段验证失败返回 422。列表接口继续返回数组。

运行权限接口回归测试（使用内存 SQLite）：

```bash
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -p 'test_permission.py' -v
```

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
> **user** 是普通用户基础角色，不可删除、不可编辑；已关联的基础角色必须保留。

### 默认权限

| 编码 | 能力 |
| --- | --- |
| `api:*` | 全局管理，包含角色与权限分配 |
| `api:user:*` | 用户查询、新增、编辑及删除 |
| `api:user:assign-role` | 在自身权限范围内分配用户角色 |
| `api:role:*` | 角色查询、新增、编辑及删除 |
| `api:role:assign-permission` | 在自身权限范围内设置角色权限 |
| `api:permission:*` | 权限目录查询、新增、编辑及删除 |
| `api:模块:read/create/update/delete` | 对应模块的单项维护能力，每项独立编码 |
| `menu:dashboard`、`menu:setting`、`menu:chart` | 工作台、设置及图表菜单 |

管理菜单由对应模块的读取能力生成；旧 `page:*`、`btn:*` 记录只作兼容保留。

角色管理员默认关联全部角色维护能力、角色权限设置能力及角色管理菜单，并拥有
`api:user:assign-role`，可在角色管理页面查看关联用户，并在自身权限范围内分配或解除用户角色。

权限管理员默认关联全部权限目录维护能力及权限管理菜单，并拥有 `api:role:read` 和
`api:role:assign-permission`，可查看引用角色并在自身权限范围内设置或解除角色权限关联。

### 举例

```bash
Users
├── admin
│   └── [admin] → api:*
├── user_admin
│   ├── [user] → menu:dashboard, menu:setting
│   └── [user_admin] → api:user:*, api:user:assign-role
├── role_admin
│   ├── [user] → menu:dashboard, menu:setting
│   └── [role_admin] → api:role:*, api:role:assign-permission, api:user:assign-role, menu:role
├── permission_admin
│   ├── [user] → menu:dashboard, menu:setting
│   └── [permission_admin] → api:permission:*, api:role:read, api:role:assign-permission, menu:permission
├── rbac_admin
│   ├── [user] → menu:dashboard, menu:setting
│   ├── [user_admin] → api:user:*, api:user:assign-role
│   ├── [role_admin] → api:role:*, api:role:assign-permission, api:user:assign-role, menu:role
│   └── [permission_admin] → api:permission:*, api:role:read, api:role:assign-permission, menu:permission
└── user
    └── [user] → menu:dashboard, menu:setting
```


```bash
# 后端接口权限
# api:[module]:[action]
api:*
api:user:*
api:user:read
api:user:assign-role
api:role:assign-permission


# 旧页面及按钮编码保留，当前管理页面和按钮使用后端 capabilities。
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

### 权限依赖示例

```python
from fastapi import Depends
from console_server.utils.auth import require_permission, require_any_permission

# 普通维护权限与授权权限分别检查。
current_user = Depends(require_permission("user", "api:user:update"))
grant_operator = Depends(require_permission("user", "api:user:assign-role"))
# 选项查询允许读取能力或独立授权能力。
options_reader = Depends(require_any_permission("api:role:read", "api:role:assign-permission"))
```

`GET/POST/PUT/DELETE` 编码仍兼容 `read/create/update/delete`，但不包含分配角色或设置权限。
`page:*` 和 `btn:*` 等旧记录保留用于兼容；当前管理页面和按钮直接使用后端的操作能力。

## 用户管理 CRUD

用户管理页面位于前端 `/admin/user`，详情页面位于 `/admin/user/:id`。

| 方法 | 接口 | 功能 |
| --- | --- | --- |
| POST | `/api/v1/user/create` | 新增用户并分配普通角色 |
| GET | `/api/v1/user/list` | 分页查询用户列表 |
| GET | `/api/v1/user/{user_id}` | 查询用户详情和角色 |
| PUT | `/api/v1/user/{user_id}/update` | 编辑用户资料、状态及可选密码 |
| DELETE | `/api/v1/user/{user_id}/remove` | 删除用户及其角色关联 |

写入字段为 `name`、`email`、`description` 和 `is_active`。新增时必须提供 `password`，编辑时不传密码则保留原密码。密码至少 6 个字符，UTF-8 编码最多 72 字节；姓名最多 100 个字符，邮箱最多 100 个字符，描述最多 128 个字符。所有响应均不包含密码或密码哈希。

接口通过 `api:user:*` 或对应请求方法权限鉴权。新增用户默认可编辑、可删除，优先分配启用的 `user` 角色，也兼容初始化 SQL 中的 `normal` 角色。管理员账号、不可编辑或不可删除的账号受到保护；操作者不能删除或停用当前登录账号，也不能修改当前登录账号的邮箱。

用户列表的“设置角色”按钮可以查看已有角色。具有 `api:user:assign-role` 权限的操作者，可以搜索自身可授予的角色、多选关联或取消关联，然后一次保存完整选择。没有角色分配权限、受保护账号及当前登录账号只允许查看。

角色通过 `user_roles` 多对多关联表与用户关联，用户获得关联角色的接口权限；停用角色不参与后端授权。已关联的 `user`、`normal` 基础角色必须保留，已停用角色可以保留或移除，但不能新增关联。

前端保存调用 `PUT /api/v1/user/{user_id}/roles`，请求体为 `{"role_ids": [2, 3]}`，需要 `api:user:assign-role` 权限，返回保存后的用户详情。关联的添加和移除在单个事务内完成。角色选择器读取 `GET /api/v1/user/{user_id}/role-options`，允许用户读取或角色分配能力，不依赖角色目录查询权限；只返回已关联及自身可授予的角色。原有 `assign-roles`、`remove-roles` 接口继续可用，并执行相同的授权范围、账号和基础角色保护。

运行用户 CRUD 回归测试（使用内存数据库，不需要启动 PostgreSQL）：

```bash
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -p 'test_user_crud.py'
```

## 角色管理接口

角色接口使用 `/api/v1/role` 前缀，需要登录及对应操作权限。

| 方法 | 路径 | 功能 | 权限 |
| --- | --- | --- | --- |
| GET | `/list` | 分页查询角色，参数为 `page`、`page_size` | `api:role:get` |
| GET | `/{role_id}/detail` | 查询角色详情、保护标记和审计时间 | `api:role:get` |
| POST | `/create` | 创建自定义角色 | `api:role:post` |
| PUT | `/{role_id}/update` | 更新显示名称、描述和启用状态 | `api:role:put` |
| DELETE | `/{role_id}/remove` | 删除未关联用户的角色，并清理自己的权限配置 | `api:role:delete` |
| GET | `/{role_id}/users` | 分页查看关联用户，不返回账号凭据 | `api:role:read`，且具有 `api:user:read` 或 `api:user:assign-role` |
| GET | `/{role_id}/permissions` | 查询角色当前关联的权限 ID | `api:role:get` |
| PUT | `/{role_id}/permissions` | 保存完整权限集合，支持取消关联及清空 | `api:role:assign-permission` |
| POST | `/{role_id}/assign-permissions` | 向角色追加权限，保留已有权限 | `api:role:assign-permission` |

角色标识创建后不可修改。新建角色默认可编辑、可删除，系统角色的保护标记由服务端校验；`admin` 始终禁止编辑和删除。
角色列表及详情返回 `user_count`；停用用户的关联也计入数量。有用户引用时返回 409，
先为用户调整角色并解除关联，再删除角色。只有权限配置、没有关联用户的角色允许删除，
删除时清理 `role_permissions`，保留权限实体。`is_deletable` 是保护标记，不代表当前没有引用。

在前端「角色管理」列表点击「权限设置」，即可查看当前关联。可编辑角色支持搜索权限、
跨页勾选或取消勾选，点击「保存」后统一更新；「清空选择」后保存会移除全部权限关联。
只读用户、受保护角色及超出操作者授权范围的角色只能查看已关联权限。选择器读取
`GET /api/v1/role/{role_id}/permission-options`，只展示已关联及自身可授予的权限，
不依赖权限目录查询能力。权限本身的不可编辑、不可删除标记不影响关联。

设置及追加请求使用 `{"permission_ids": [1, 2]}`，ID 必须是正整数且全部存在。
设置接口接受空数组，追加接口遇到空数组保持原关联不变。所有变更在一次事务中完成，
重复 ID 自动去重；只修改关联，保留角色用户和权限记录。角色受保护时，设置及追加均返回 400。

角色接口回归测试使用内存 SQLite，不依赖开发数据库：

```bash
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -p 'test_role_crud.py' -v
```

## 列表搜索

管理页面的条件搜索由后端执行，文本不区分大小写并按包含关系匹配。
多个条件使用 AND；`keyword` 在名称、显示名称或邮箱、描述之间使用 OR。

| 接口 | 查询条件 |
| --- | --- |
| `/api/v1/user/list` | `name`、`email`、`is_active`、`keyword` |
| `/api/v1/role/list` | `name`、`display_name`、`is_active`、`is_editable`、`is_deletable`、`keyword` |
| `/api/v1/permission/page` | `name`、`display_name`、`is_editable`、`is_deletable`、`keyword` |

分页使用 `page`（从 1 开始）和 `page_size`（最大 100），返回
`items`、`total`、`page`、`page_size`、`total_pages`。
`total` 和 `total_pages` 根据筛选后的记录计算。

例如 `/api/v1/role/list?name=audit&is_active=false&page=1&page_size=10`
查询标识包含 `audit` 的禁用角色。布尔条件使用 `true` 或 `false`；
省略条件或传入空白文本表示不筛选。文本中的 `%` 和 `_` 按字面字符匹配。

`/api/v1/permission/list` 也接受权限搜索条件，继续返回完整数组供权限目录查询使用。
用户和角色查询沿用现有接口授权，权限查询需要登录及权限目录读取能力。
新增搜索无需修改数据库表结构。

运行全部接口回归测试（内存 SQLite）：

```bash
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
```

## 配置

```toml
[tool.hatch.build.targets.wheel]
packages = ["src.console_server"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```


## 权限设计与数据库初始化

授权只取启用用户的启用角色权限，角色名称不授予隐含能力。`api:*` 显式表示全局管理权限。
`api:user:*` 和 `api:role:*` 只覆盖查询、新增、资料编辑和删除，授权操作需要独立编码。

| 操作 | 权限编码 |
| --- | --- |
| 查询、新增、编辑、删除用户 | `api:user:read/create/update/delete`（每个操作是单独权限） |
| 分配、替换、移除用户角色 | `api:user:assign-role` |
| 查询、新增、编辑、删除角色 | `api:role:read/create/update/delete`（每个操作是单独权限） |
| 追加、替换、清空角色权限 | `api:role:assign-permission` |
| 查询、新增、编辑、删除权限定义 | `api:permission:read/create/update/delete`（每个操作是单独权限） |

授权人员只能授予自身已有的权限；不能通过给其他账号分配全局角色、修改自身所在角色、
重启超出范围的停用角色或更改权限编码扩大能力。角色整体权限更新也不能移除超出自身范围的已有权限。
用户角色整体设置保留基础角色；不可授予的已有角色可以保留，但不能新增或移除。
用户资料编辑、密码重置及删除同样检查目标账号的角色权限范围。
权限编码创建后不可修改，显示名称、描述和操作保护标记仍可编辑；删除权限定义检查权限范围。

`GET /api/v1/user/{id}/role-options` 只返回已关联和可授予的角色，并返回 `grantable_role_ids`。
`GET /api/v1/role/{id}/permission-options` 只返回已关联和可授予的权限，并返回
`grantable_permission_ids` 和 `editable`。选项接口不依赖另一个模块的查询能力。
权限目录的列表、分页和详情查询均需要权限读取能力。

`GET /api/v1/self/current` 返回有效角色、权限和 `capabilities`。
管理菜单由对应模块的读取能力推导，路由和按钮使用同一份 `capabilities`。
直接进入无权访问的管理页面显示 403。前端在授权相关写入成功后立即同步权限，
并继续在每 30 秒、窗口恢复或接口拒绝时重新读取权限，
后端每次请求读取数据库，授权撤销立即影响后续请求。
授权关联、启用状态和删除操作的审计保存在 `authorization_audits`，与修改同事务提交。

项目处于开发阶段，数据库使用 `sql/init.sql` 一次性初始化，包含当前权限目录、系统角色、授权能力及审计表。
内置用户管理员显式关联全部 `api:user:*` 前缀权限（含查询、新增、编辑、删除及角色分配）和 `menu:user` 菜单权限。
内置角色管理员显式关联全部 `api:role:*` 前缀权限、`menu:role` 菜单及 `api:user:assign-role`，授权操作受自身权限范围限制。
自定义维护角色需要管理员显式分配授权能力。

### 使用中的权限和角色禁止删除

在权限列表、角色列表或详情中点击关联数量，可以分页查看引用对象，再进入现有的
「权限设置」或「设置角色」弹窗解除关联。保存后刷新关联数量及当前登录用户的有效权限。
查看关联对象需具备对应模块的查询或授权能力；设置仍遵循原有账号、基础角色和授权范围保护。
前端禁用使用中对象的删除按钮，后端独立校验，避免旧列表或直接调用 API 绕过限制。

删除失败示例（HTTP 409）：

```json
{"detail": {"code": "permission_in_use", "message": "该权限已关联 3 个角色，请先解除关联后再删除", "role_count": 3}}
```

角色引用错误使用 `code=role_in_use` 和 `user_count`。删除与新增关联在同一实体行上加锁，
获得锁后重新查询引用；同时修改多个对象时按 ID 排序锁定。删除及审计同事务提交，
失败完整回滚。删除审计保存对象名称、显示名称及角色原权限快照，便于删除后追溯。

新库的 ORM 和初始化 SQL 使用相同外键规则：`user_roles.role_id` 与
`role_permissions.permission_id` 为 `ON DELETE RESTRICT`；用户删除清理自己的角色关联，
无用户引用的角色删除清理自己的权限配置。引用检查通过中间表索引和批量统计完成。

PostgreSQL 并发及外键回归测试使用随机独立 schema，结束后自动清理。
为测试连接设置 `CONSOLE_TEST_DATABASE_URL`，再运行：

```bash
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -p 'test_deletion_concurrency.py' -v
```

未设置测试连接时，这组 PostgreSQL 测试跳过，其余内存 SQLite 接口测试正常执行。

运行全部接口和授权边界回归测试：

```bash
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -p 'test_*.py'
```

### 用户 UUID 与令牌用途

JWT 的 `sub` 使用用户的持久 UUID，`token_use` 分别为 `access` 和 `refresh`。
业务接口仅接受访问令牌，刷新接口仅接受刷新令牌；两者都必须具备有效的
`exp`、规范 UUID 格式的 `sub` 和正确用途。旧邮箱令牌以及缺少用途的旧令牌全部拒绝，
部署后用户需要重新登录。接口中的数值用户 ID 和现有角色关联保持兼容。

用户 UUID 由服务端生成，数据库禁止修改。`user_identities` 永久保留已使用的 UUID，
删除或重建账号不能复用旧 UUID；修改邮箱不会改变登录身份。此表只保存 UUID 和创建时间。

`sql/init.sql` 已包含新库所需的 UUID 字段及身份约束。UUID 和鉴权回归测试：

```bash
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -p 'test_auth_tokens.py' -v
```


### 环境密钥与登录会话

`SECRET_KEY` 不再有源码默认值。开发默认读取 `.env.dev`（缺失时兼容 `.env`），预发、生产和测试分别读取 `.env.pre`、`.env.prod`、`.env.test`。`ENV_FILE` 可指定配置文件，系统环境变量优先于文件；启动器已选择的文件不会被应用二次加载覆盖。所有环境必须配置至少 32 字节的随机密钥，空值、短密钥与示例占位值会使应用启动失败。生产环境的数据库地址也必须显式配置。

示例文件中的 `SECRET_KEY=` 留空，复制后为每个环境单独生成密钥，实际环境文件不提交 Git：

```bash
python -c "import secrets; print(secrets.token_urlsafe(64))"
```

`sql/init.sql` 同时初始化用户 UUID、登录会话表及会话撤销触发器，无需额外升级脚本。

新令牌必须包含不可变用户 UUID `sub`、登录会话 UUID `sid`、用途 `token_use` 和每次签发独立生成的随机 UUID `jti`。`auth_sessions` 只保存当前刷新 `jti` 与会话状态，不保存原始令牌；`sid` 标识同一次登录的令牌家族。刷新先锁用户、再锁会话，并通过匹配旧 `jti`、未撤销状态和未过期时间的条件更新消费刷新令牌，只在事务提交成功后返回新凭证。重放旧刷新凭证撤销整次登录，包括已经换发的访问与刷新令牌。刷新令牌的 `exp` 始终等于首次登录时确定的会话截止时间，访问令牌也不会超过该时间，默认上限为 `REFRESH_TOKEN_EXPIRE_DAY`（30 天）。

登录、刷新和退出接口统一使用 `POST`，要求 `Content-Type: application/json` 和 `X-CSRF-Protection: 1`。拒绝跨站请求，并精确校验 `Origin`；没有 `Origin` 时检查 `Referer`。同站子域不会自动获得信任。没有浏览器来源头的程序客户端仍需携带上述非简单请求头。

同源部署默认与请求公开来源匹配；反向代理应保留公开 Host 和协议。代理无法保留来源时，通过环境变量明确指定前端来源（JSON 数组，不支持通配符），例如：

```dotenv
AUTH_ALLOWED_ORIGINS=["https://console.example.com"]
```

配置后仅信任列出的来源。所有认证响应（包括失败和协议错误）带 `Cache-Control: no-store` 和 `Pragma: no-cache`；认证凭证失败使用 401，CSRF 拒绝使用 403，业务权限不足仍使用 403。

| 接口 | 行为 |
| --- | --- |
| `/api/auth/refresh` | 使用 HttpOnly Cookie 换发令牌；无效凭证返回标准 401 并清除 Cookie |
| `/api/auth/logout` | 幂等撤销提交凭证所属会话；访问令牌过期时仍可退出 |
| `/api/auth/logout-all` | 使用有效访问令牌退出当前账号的全部登录会话 |

用户密码变更和停用由数据库触发器在同一事务内撤销全部会话，重新启用不会恢复旧令牌。删除账号通过外键删除会话；过期会话随定时清理任务删除。`TokenBlacklist` 保留兼容单令牌撤销。

鉴权先验证 JWT 签名、用途和必要声明，再查询数据库；中间件和依赖复用同一请求的已验证 payload，并且缓存必须匹配原始令牌和用途。用户、会话和黑名单合并到一个查询，角色和权限仍在每次请求中预加载，停用、撤销和授权修改继续实时生效，不使用跨请求授权缓存。

前端访问令牌仅保存在内存中，页面重载通过 HttpOnly Cookie 恢复。`rememberMe=true` 仅控制刷新 Cookie 持久化；未勾选时使用浏览器会话 Cookie。加载新版前端会删除原来 localStorage/sessionStorage 中的 `access_token`、`token_type` 和包含密码的 `rememberMe` 数据。退出保留语言与主题偏好，跨标签页仅广播退出事件。登录、刷新和退出共享 Web Lock；不支持 Web Locks 时使用 IndexedDB 的原子租约，记录锁持有者及到期时间，不保存凭证。租约定期续期，关闭标签页留下的过期锁可回收；失去锁时拒绝迟到结果。协调存储不可用时不发送会话变更请求。请求超时通过 AbortSignal 结束，较慢旧请求的 401 优先复用已有新令牌。

会话回归包含多数据库连接竞争、刷新提交失败回滚、刷新与退出竞争、重放撤销、绝对期限、CSRF、单次验签及实时权限：

```bash
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests
# 配置独立测试库才能运行 PostgreSQL 并发及初始化用例；用例在随机 schema 中运行。
CONSOLE_TEST_DATABASE_URL=postgresql+asyncpg://user:password@localhost/testdb PYTHONPATH=src .venv/bin/python -m unittest discover -s tests
```

首次部署时先在目标环境的空库导入 `sql/init.sql`，再部署前后端。


## DeepSeek AI 对话

已接入 `/api/v1/chat/stream` 流式接口与 `ai_user` 独立角色。配置、数据库增量 SQL、授权及启动方法见 [DeepSeek 流式对话接入](docs/DeepSeek流式对话接入.md)。
