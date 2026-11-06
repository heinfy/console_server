#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"

# 优先使用显式指定或已激活的 Python，然后使用项目虚拟环境。
if [[ -n "${PYTHON_BIN:-}" ]]; then
  python_bin="$PYTHON_BIN"
elif [[ -n "${VIRTUAL_ENV:-}" && -x "$VIRTUAL_ENV/bin/python" ]]; then
  python_bin="$VIRTUAL_ENV/bin/python"
elif [[ -x "$PROJECT_ROOT/.venv/bin/python" ]]; then
  python_bin="$PROJECT_ROOT/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  python_bin="python3"
else
  printf '%s\n' '未找到 Python，请先执行 uv sync 或设置 PYTHON_BIN。' >&2
  exit 1
fi

exec "$python_bin" "$PROJECT_ROOT/scripts/start.py" "$@"
