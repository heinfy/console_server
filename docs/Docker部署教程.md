# Docker 部署教程

本教程用于 `console_server` 的预发和生产部署，所有命令在后端项目根目录执行。
两个环境使用同一个镜像，由运行时的 `ENV`、数据库连接和密钥区分。
默认 `docker-compose.yaml` 仅启动本地数据库，部署后端使用 `scripts/start.sh 环境 --docker`，
由入口自动选择 `docker-compose.deploy.yaml` 和对应环境文件。

## 1. 准备配置与数据库

首次创建配置文件，保留已有配置：

```bash
cp -n .env.pre.example .env.pre
cp -n .env.prod.example .env.prod
python3 -c 'import secrets; print(secrets.token_urlsafe(64))'
```

为每个环境分别生成并保存独立密钥，填写对应环境文件：

| 配置 | 填写方式 |
| --- | --- |
| `DATABASE_URL` | 使用 `postgresql+asyncpg://用户:密码@数据库主机:5432/库名`，预发与生产使用独立数据库；用户名和密码中的 URL 特殊字符需编码 |
| `SECRET_KEY` | 至少 32 字节的随机密钥，同一环境实例共享；不要每次发版重新生成 |
| `DEBUG` / `RELOAD` | 均为 `false` |
| `WORKERS` | 保持 `1`；当前每个进程和副本都会启动定时清理任务 |
| `COOKIE_SECURE` | HTTPS 部署使用 `true` |
| `AUTH_ALLOWED_ORIGINS` | 替换为真实前端 Origin，例如 `["https://pre.example.com"]`；用于刷新、退出接口的请求来源校验 |
| `FORWARDED_ALLOW_IPS` | 填写容器实际看到的反向代理 IP / 网段，确保 HTTPS 转发信息被识别 |

部署 Compose 连接已准备好的 PostgreSQL，不创建数据库，也不自动初始化表结构。
容器内的 `localhost` 指向后端容器本身；另一容器中的数据库应使用共享 Docker 网络中的服务名，
外部数据库使用容器可达的主机地址。使用 Docker Desktop 连接宿主机数据库时可用
`host.docker.internal`；Linux 需要自行配置宿主机地址或 `host-gateway` 映射。

首次部署使用新的空库，并按第 3 节导入 `sql/init.sql`。
环境文件仅保存在部署主机，不提交 Git，也不复制进镜像；限制文件读取权限：

```bash
chmod 600 .env.pre .env.prod
```

## 2. 构建同一个镜像

```bash
docker build -t console-server:release .
```

Dockerfile 使用项目指定的 Python `3.12.15` 和固定版本 uv，在构建阶段执行
`uv sync --frozen --no-dev --no-install-project`。运行镜像包含依赖、源码、启动脚本及初始化 SQL，
通过 `scripts/start.sh` 启动；运行时不安装依赖，也不包含 uv 和编译工具。
`uv.lock` 与 `uv.toml` 当前使用腾讯 PyPI 镜像，构建机器需要能够访问该源及基础镜像仓库。
uv 也在构建阶段从该源安装；如使用其他 uv 下载源，可传入 `--build-arg UV_INDEX_URL=源地址`。
项目依赖源仍由 `uv.lock` 和 `uv.toml` 决定。

构建平台应与服务器架构匹配；在 Apple Silicon 上为常见 x86 服务器构建时使用：

```bash
docker buildx build --platform linux/amd64 -t console-server:release --load .
```

## 3. 首次部署初始化数据库

项目当前尚未部署预发和生产环境，首次部署先为目标环境创建独立的空数据库，再导入 `sql/init.sql`。
以下示例在可连接 PostgreSQL 的主机上执行，使用标准 PostgreSQL 连接串，将用户、主机和库名替换为目标环境配置：

```bash
psql "postgresql://用户@数据库主机:5432/console_pre" -v ON_ERROR_STOP=1 -f sql/init.sql
```

生产环境使用独立的 `console_prod` 数据库。初始化文件已包含当前权限目录、系统角色、用户 UUID、
登录会话、删除外键约束及审计表，完成后即可启动后端。
`sql/init.sql` 包含重建语句，只用于首次初始化空库；应用重启和后续镜像发布无需重复导入。

## 4. 用 Compose 启动预发和生产

宿主机上的统一入口需要 Bash、Python 和 `python-dotenv`，可先执行 `uv sync --frozen --no-dev`。
启动脚本使用项目 `.venv`，先通过应用共用的 `environment.py` 加载配置，再运行 Docker Compose。
容器内继续通过同一个 `start.sh` 启动 Uvicorn。直接使用第 5 节的 `docker run` 时无需宿主机 Python 依赖。

使用第 2 节构建的镜像启动预发：

```bash
CONSOLE_IMAGE=console-server:release bash scripts/start.sh pre --docker up -d --no-build
bash scripts/start.sh pre --docker ps
bash scripts/start.sh pre --docker logs --tail=100 backend
curl --fail http://127.0.0.1:8000/api/health
```

生产在生产主机上运行：

```bash
CONSOLE_IMAGE=console-server:release bash scripts/start.sh prod --docker up -d --no-build
```

初次试用也可以省略 `CONSOLE_IMAGE`，把 `--no-build` 改为 `--build`，由 Compose 构建本地镜像。
`--docker` 后的参数全部传给 Compose，不带参数时默认执行 `up -d`；
`--env-file`、`--dry-run` 等启动器选项写在 `--docker` 前。
入口按环境自动使用 `console-pre`、`console-prod` 等项目名。后续 `up`、`run`、`exec` 或 `pull`
继续使用相同环境和镜像标签；`ps`、`logs` 只查看状态，但仍会加载并检查配置。

```bash
# 指定外部配置文件，不必额外指定环境文件变量
bash scripts/start.sh pre --env-file /etc/console/pre.env --docker up -d --no-build

# 检查配置并打印 Compose 命令，不启动服务
bash scripts/start.sh pre --dry-run --docker
```

`PORT` 同时决定应用监听端口和容器映射目标，默认 `8000`；宿主机默认也发布同一端口，
只绑定 `127.0.0.1`，供同一主机上的 Nginx 等代理访问。环境文件中的 `PORT=8080`
会得到 `127.0.0.1:8080` 到容器 `8080` 的映射，健康检查地址也需相应改为 `8080`。
外部用户通过 HTTPS 域名访问，前端与 `/api/` 建议由同一域名转发。
代理需保留 `Host` 并设置 `X-Forwarded-For`、`X-Forwarded-Proto`；
`FORWARDED_ALLOW_IPS` 应匹配容器看到的来源，宿主机代理经过 Docker 网桥时通常不是 `127.0.0.1`。
`AUTH_ALLOWED_ORIGINS` 只负责来源校验，浏览器跨域访问还需要网关提供相应 CORS 配置。

在同一主机并行运行两套环境，环境参数会区分项目名，再设置不同宿主机端口。例如预发使用 `8001`：

```bash
HOST_PORT=8001 CONSOLE_IMAGE=console-server:release \
  bash scripts/start.sh pre --docker up -d --no-build
curl --fail http://127.0.0.1:8001/api/health
```

直接启动和 Docker 启动都遵循“系统变量 > 所选配置文件 > 默认值”。Compose 额外使用以下部署变量：

| 变量 | 默认值 / 作用 |
| --- | --- |
| `CONSOLE_IMAGE` | `console-server:local`，可替换为镜像仓库中的固定版本标签 |
| `PORT` | `8000`，应用监听及容器转发目标端口，从所选环境文件或系统变量读取 |
| `HOST_PORT` | 默认与 `PORT` 相同；只改变宿主机转发端口 |
| `BIND_ADDRESS` | `127.0.0.1`；需从主机外访问发布端口时显式设置所需地址 |

平台要求容器监听 `8080` 时，在所选环境文件填写 `PORT=8080`，或者启动时覆盖：

```bash
PORT=8080 bash scripts/start.sh pre --docker up -d --build
```

使用 `docker run` 或 Kubernetes 时同样注入 `PORT=8080`，同步调整服务端口。
镜像的健康检查会自动使用实际配置的 `PORT` 和 `API_STR`。

## 5. 用 docker run 或配置文件挂载

不使用 Compose 时，可直接启动：

```bash
docker run -d --name console-pre --restart unless-stopped \
  --env-file .env.pre -e ENV=pre \
  -p 127.0.0.1:8000:8000 \
  console-server:release
```

如果配置文件中的 `PORT` 是 `8080`，相应使用 `-p 127.0.0.1:8000:8080`。
`docker run --env-file` 将文件值注入环境变量；列表配置保持原始 JSON，
例如 `AUTH_ALLOWED_ORIGINS=["https://pre.example.com"]`，不要给整个值额外加引号。

也可以只读挂载外部配置文件：

```bash
docker run -d --name console-pre --restart unless-stopped \
  -e ENV=pre -e ENV_FILE=/etc/console/runtime.env \
  --mount type=bind,src=/etc/console/pre.env,dst=/etc/console/runtime.env,readonly \
  -p 127.0.0.1:8000:8000 \
  console-server:release
```

容器使用 UID/GID `10001`，挂载文件需允许该用户读取。Linux 部署主机可将配置文件归组到
GID `10001`，并设置 `640`，由部署管理员管理。挂载模式同样遵循“系统环境变量优先于文件值”。
修改环境文件后需要重新创建容器；单纯重启不会重新注入 `--env-file` 中的新值。

## 6. 健康、日志与发布

```bash
docker inspect --format '{{.State.Health.Status}}' console-pre
docker logs --tail=100 console-pre
docker stop --time=30 console-pre
```

Compose 的实际容器名可从 `bash scripts/start.sh pre --docker ps` 读取。
健康检查调用 `/api/health`，确认 HTTP 服务能响应，不验证数据库连通性和表结构。
发布前还应验证登录、刷新会话和实际读写接口。Docker 的 `unhealthy` 状态本身不会触发
`restart: unless-stopped`；该策略用于进程退出，健康异常恢复由运维监控或容器平台处理。

Compose 配置日志轮转并给予 30 秒优雅停止时间。先保持单进程、单副本部署，
后续扩容前处理重复定时任务。
正式发布把 `CONSOLE_IMAGE` 设置为仓库中的明确版本，拉取镜像后执行 `up -d --no-build`；
预发验证通过后将相同镜像版本用于生产。回滚时需要确认目标镜像与当前数据库表结构兼容。
