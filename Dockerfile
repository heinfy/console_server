# 使用官方 Python 3.12 镜像作为基础镜像
FROM python:3.12-slim

# 设置工作目录
WORKDIR /app

# 安装系统依赖
RUN apt-get update && apt-get install -y \
  curl \
  gcc \
  postgresql-client \
  && rm -rf /var/lib/apt/lists/*

# 复制依赖文件
COPY pyproject.toml ./

# 使用 pip 安装 Python 依赖
RUN pip install --no-cache-dir fastapi uvicorn asyncpg sqlalchemy greenlet

# 复制应用代码
COPY src/ ./src/

# 暴露端口
EXPOSE 8000

# 设置环境变量
ENV PORT=8000
ENV CORS_ALLOW_ORIGIN="*"

# 启动命令
CMD ["uvicorn", "src.console_server.main:app", "--port", "8000", "--forwarded-allow-ips", "*", "--host", "0.0.0.0"]
