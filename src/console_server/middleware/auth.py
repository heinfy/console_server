from fastapi import Request, Response, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from console_server.core.config import settings
from jose import JWTError, jwt
import re

from console_server.utils.auth import get_current_user

# 定义不需要认证的路径列表
EXCLUDED_PATH_PATTERNS = [
    r"^/docs$",  # 精确匹配 /docs 路径
    r"^/api/openapi.json$",  # 精确匹配 /api/openapi.json 路径
    r"^/api/auth/.*$",  # 匹配所有 /api/auth/ 开头的路径
    r"^/api/health$",  # 精确匹配 /api/health 路径
]


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        # 检查当前请求路径是否匹配任何排除模式
        path = request.url.path
        is_excluded = any(re.match(pattern, path) for pattern in EXCLUDED_PATH_PATTERNS)

        if is_excluded:
            # 直接调用下一个处理器，跳过身份验证
            return await call_next(request)

        # 对于不在排除列表中的路径，执行原有的身份验证逻辑
        token = request.headers.get("Authorization")

        if not token:
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": "Unauthorized: Missing or invalid token"},
            )
        try:
            # 移除Bearer前缀（如果有）
            if token.startswith("Bearer "):
                token = token[7:]

            payload = jwt.decode(
                token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM]
            )

            # 将 payload 信息附加到请求状态中供后续使用
            request.state.user = payload
        except JWTError as e:
            print(f"JWT decode error: {e}")
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": f"Unauthorized: Invalid token format - {str(e)}"},
            )
        except Exception as e:
            print(f"Unexpected error: {e}")
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": "Unauthorized: Token validation failed"},
            )

        # 继续处理请求
        return await call_next(request)
