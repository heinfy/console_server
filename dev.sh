#!/usr/bin/env bash
export CORS_ALLOW_ORIGIN="http://localhost:5173;http://localhost:8000"
PORT="${PORT:-8000}"
uvicorn console_server.main:app --app-dir src --port $PORT --forwarded-allow-ips '*' --host 0.0.0.0 --reload
