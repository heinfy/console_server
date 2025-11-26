from fastapi import Request, Response, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from console_server.core.config import settings
from jose import JWTError, jwt
import re

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
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"detail": "Unauthorized: Missing or invalid token"},
            )
        try:
            # 移除Bearer前缀（如果有）
            if token.startswith(f"{settings.TOKEN_TYPE} "):
                token = token[7:]
            # 检查 token 格式是否正确（应该有3个部分）
            if len(token.split(".")) != 3:
                return JSONResponse(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    content={"detail": "Unauthorized: Invalid token format"},
                )

            # 解码和验证 JWT token
            payload = jwt.decode(
                token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM]
            )

            is_active = payload.get("is_active")
            if is_active is False:
                return JSONResponse(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    content={"detail": "Unauthorized: Inactive user"},
                )
            # 将 payload 信息附加到请求状态中供后续使用
            request.state.user = payload
        # 处理 JWT 解码错误
        except JWTError as e:
            print(f"JWT decode error: {e}")
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": f"Unauthorized: Invalid token format - {str(e)}"},
            )
        # 处理其他异常
        except Exception as e:
            print(f"Unexpected error: {e}")
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"detail": "Unauthorized: Token validation failed"},
            )

        # 继续处理请求
        return await call_next(request)
