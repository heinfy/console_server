# Console Server

FastAPI 控制台服务器项目。

## 功能特性

- FastAPI 框架
- PostgreSQL 异步数据库
- SQLAlchemy ORM
- 用户管理 API

## 开发环境

### 使用 Docker Compose（推荐）
```bash
# PostgreSQL 18+ Docker 镜像引入的一个重大变更（breaking change）。
# 它不再将数据直接存放在 /var/lib/postgresql/data，而是改用与 pg_ctlcluster（Debian/Ubuntu PostgreSQL 管理工具）兼容的目录结构：
docker run -d --name my-postgres-18 \
  -e POSTGRES_PASSWORD=123456 \
  -p 5432:5432 \
  -v ~/postgres-data:/var/lib/postgresql \
  postgres:latest
```

```toml
[tool.hatch.build.targets.wheel]
packages = ["src.console_server"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```