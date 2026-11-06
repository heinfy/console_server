ARG PYTHON_IMAGE=python:3.12.15-slim

FROM ${PYTHON_IMAGE} AS builder
ARG UV_VERSION=0.11.1
ARG UV_INDEX_URL=https://mirrors.cloud.tencent.com/pypi/simple
WORKDIR /app
ENV UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0 \
    UV_COMPILE_BYTECODE=1

RUN python -m pip install --no-cache-dir --index-url "${UV_INDEX_URL}" "uv==${UV_VERSION}"

# 构建时安装锁定的完整运行依赖，源码改动可复用这一层。
COPY pyproject.toml uv.lock uv.toml ./
RUN uv sync --frozen --no-dev --no-install-project

FROM ${PYTHON_IMAGE} AS runtime
WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH=/app/src \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    RUNNING_IN_DOCKER=1

RUN groupadd --gid 10001 console \
    && useradd --uid 10001 --gid console --no-create-home --shell /usr/sbin/nologin console

COPY --from=builder /app/.venv /app/.venv
COPY pyproject.toml ./
COPY src/ ./src/
COPY scripts/ ./scripts/
COPY sql/ ./sql/
USER console

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "/app/scripts/healthcheck.py"]

CMD ["bash", "/app/scripts/start.sh"]
