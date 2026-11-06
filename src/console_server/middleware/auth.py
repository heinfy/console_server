from fastapi import Request, Response, status
from fastapi.responses import JSONResponse
from fastapi.security.utils import get_authorization_scheme_param
from jose import JWTError
from starlette.middleware.base import BaseHTTPMiddleware

from console_server.core.config import settings
from console_server.utils.auth import verified_token_payload


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path
        prefix = settings.API_STR.rstrip("/")
        auth_path = path.startswith(f"{prefix}/auth/")
        excluded = auth_path or path in {"/docs", f"{prefix}/openapi.json", f"{prefix}/health"}
        if not excluded:
            scheme, token = get_authorization_scheme_param(request.headers.get("Authorization"))
            try:
                if scheme.lower() != "bearer" or not token:
                    raise JWTError("Missing bearer token")
                # 路由依赖复用本次校验的 payload，继续检查实时会话和授权状态。
                verified_token_payload(token, "access", request)
            except JWTError:
                return JSONResponse(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    content={"detail": "无法验证凭证，请登录"},
                    headers={"WWW-Authenticate": settings.TOKEN_TYPE},
                )
        response = await call_next(request)
        if auth_path:
            # 包括协议错误和失败响应，避免认证响应进入浏览器或代理缓存。
            response.headers["Cache-Control"] = "no-store"
            response.headers["Pragma"] = "no-cache"
        return response
