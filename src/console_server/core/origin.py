"""规范化精确来源，避免端口、大小写或 URL 前缀比较造成来源误判。"""

from urllib.parse import urlsplit


def normalize_origin(value: str, *, allow_path: bool = False) -> str:
    parsed = urlsplit(value)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or (not allow_path and (parsed.path not in {"", "/"} or parsed.query or parsed.fragment))):
        raise ValueError("来源必须是完整的 HTTP(S) origin，不支持通配符")
    host = parsed.hostname.lower()
    if "*" in host or any(char.isspace() for char in value):
        raise ValueError("无效的来源")
    port = parsed.port
    if ":" in host:
        host = f"[{host}]"
    if port is not None and port != (443 if parsed.scheme == "https" else 80):
        host = f"{host}:{port}"
    return f"{parsed.scheme}://{host}"
