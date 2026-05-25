"""
auth.py — API Key 认证中间件

认证方式：
  HTTP 请求：  Authorization: Bearer <key>
  WebSocket / SSE：?token=<key> 查询参数

豁免路径：/  /m  /sw.js  /api/health  /static/*

Key 加载顺序：
  1. 环境变量 CODERFLEET_API_KEY（空字符串 = 禁用认证）
  2. WORKSPACE_DIR/api_key.txt（不存在则自动生成并保存）
"""
from __future__ import annotations

import os
import secrets
from pathlib import Path
from urllib.parse import parse_qs

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

_KEY: str | None = None

_EXEMPT_PATHS = frozenset({"/", "/m", "/sw.js", "/api/health"})
_EXEMPT_PREFIXES = ("/static/",)


def load_api_key(workspace_dir: Path) -> str:
    global _KEY
    if _KEY is not None:
        return _KEY

    env_val = os.environ.get("CODERFLEET_API_KEY")
    if env_val is not None:
        _KEY = env_val
        if not _KEY:
            print("[CoderFleet] 认证已禁用（CODERFLEET_API_KEY 为空）")
        return _KEY

    key_file = workspace_dir / "api_key.txt"
    if key_file.exists():
        _KEY = key_file.read_text().strip()
    else:
        _KEY = secrets.token_urlsafe(32)
        key_file.write_text(_KEY + "\n")
        try:
            key_file.chmod(0o600)
        except OSError:
            pass
        print(f"\n[CoderFleet] 已生成 API Key：{_KEY}")
        print(f"[CoderFleet] 保存至：{key_file}\n")

    return _KEY


def _is_exempt(path: str) -> bool:
    return path in _EXEMPT_PATHS or any(path.startswith(p) for p in _EXEMPT_PREFIXES)


def _extract_token(scope: Scope) -> str:
    headers: dict[bytes, bytes] = dict(scope.get("headers", []))
    auth = headers.get(b"authorization", b"").decode()
    if auth.startswith("Bearer "):
        return auth[7:]

    qs = scope.get("query_string", b"").decode()
    params = parse_qs(qs)
    return params.get("token", [""])[0]


class AuthMiddleware:
    def __init__(self, app: ASGIApp, api_key: str) -> None:
        self.app = app
        self.api_key = api_key

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not self.api_key or scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        if _is_exempt(scope.get("path", "")):
            await self.app(scope, receive, send)
            return

        if _extract_token(scope) == self.api_key:
            await self.app(scope, receive, send)
            return

        if scope["type"] == "http":
            resp = JSONResponse({"detail": "未授权，请提供有效 API Key"}, status_code=401)
            await resp(scope, receive, send)
        else:
            msg = await receive()
            if msg.get("type") == "websocket.connect":
                await send({"type": "websocket.close", "code": 1008})
