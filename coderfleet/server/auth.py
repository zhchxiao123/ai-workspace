"""
auth.py — API Key 认证中间件

认证方式：
  HTTP 请求：  Authorization: Bearer <key>
  WebSocket / SSE：?token=<key> 查询参数

豁免路径：/  /m  /sw.js  /api/health  /static/*  /mcp/*

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
# /mcp/*（issue #69 Slice 2 的 Intervention 桥接）：调用方是容器里的 Claude 自己的
# MCP client，它不知道、也不该知道这把保护全站的 API Key——那把 key 的信任范围是
# "这是操作者本人的 Web UI/CLI"，把它塞进每个项目容器的环境变量里当凭证反而是更
# 严重的暴露（等于让任何一个容器都能拿它冒充操作者调用全站任何接口，而不只是
# /mcp 这一个范围很窄的能力）。/mcp 自己的认领机制是 X-CoderFleet-Task-Id
#（见 mcp_bridge.py），信任边界本来就比全站 API Key 窄，这里豁免不是放宽这道
# 认证，是承认它一直就该走另一套更窄的认证。
_EXEMPT_PREFIXES = ("/static/", "/mcp/")


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
