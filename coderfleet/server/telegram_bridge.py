"""
telegram_bridge.py — Telegram 通知与续聊桥

任务到达终态后向 Telegram 播报（文本/语音），并通过 getUpdates 长轮询
接收用户回复，续聊到对应的任务链（Conversation）。

这是一个 deep module：调用方只需认识 notify_task() / poll_once() /
send_test_message()；Bot API 的 wire 细节、代理、状态持久化全部藏在内部。

配置来自 config.conf 的 TELEGRAM_* 键，每次操作时惰性读取——
改配置即时生效，无需重启 server：
    TELEGRAM_BOT_TOKEN     BotFather 签发的 bot token
    TELEGRAM_CHAT_ID       白名单 chat id，逗号分隔
    TELEGRAM_PROXY         可选，出站代理（api.telegram.org 国内不可直连）
    TELEGRAM_NOTIFY_MODE   off | text | voice（默认 off）
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

import httpx

from coderfleet.config import load_config
from coderfleet.server.models import Task, TaskStatus

logger = logging.getLogger(__name__)

_API_BASE = "https://api.telegram.org"

_STATUS_LABELS = {
    TaskStatus.done:   "✅ 任务完成",
    TaskStatus.failed: "❌ 任务失败",
    TaskStatus.killed: "⚠️ 任务已终止",
}

# 映射条目超过上限后按插入序淘汰——只需要覆盖"最近可回复"的窗口
_MAX_MESSAGE_MAPPINGS = 200


class TelegramError(RuntimeError):
    """Telegram Bot API 调用失败（未配置、网络错误、上游返回非 ok 等）。"""


class TelegramBridge:
    def __init__(
        self,
        workspace_dir: Path,
        *,
        transport: Optional[httpx.BaseTransport] = None,
    ) -> None:
        self.workspace_dir = workspace_dir
        self.state_path = workspace_dir / "telegram_state.json"
        # 仅供测试注入 httpx.MockTransport；真实路径永远为 None。
        self._transport = transport

    # ── 配置（每次惰性读取，改动即时生效） ─────────────────────

    def _cfg(self) -> dict[str, str]:
        return load_config(self.workspace_dir)

    @property
    def token(self) -> str:
        return self._cfg().get("TELEGRAM_BOT_TOKEN", "").strip()

    @property
    def chat_ids(self) -> list[str]:
        raw = self._cfg().get("TELEGRAM_CHAT_ID", "")
        return [c.strip() for c in raw.split(",") if c.strip()]

    @property
    def proxy(self) -> str:
        return self._cfg().get("TELEGRAM_PROXY", "").strip()

    @property
    def notify_mode(self) -> str:
        mode = self._cfg().get("TELEGRAM_NOTIFY_MODE", "off").strip().lower()
        return mode if mode in ("off", "text", "voice") else "off"

    def is_configured(self) -> bool:
        """未配置时所有入口应优雅降级为 no-op，而不是抛错。"""
        return bool(self.token and self.chat_ids)

    # ── 状态持久化（offset + message→conversation 映射） ──────

    def _load_state(self) -> dict[str, Any]:
        if self.state_path.exists():
            try:
                return json.loads(self.state_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {"offset": 0, "messages": {}, "last_conversation_id": ""}

    def _save_state(self, state: dict[str, Any]) -> None:
        messages = state.get("messages", {})
        if len(messages) > _MAX_MESSAGE_MAPPINGS:
            keep = list(messages)[-_MAX_MESSAGE_MAPPINGS:]
            state["messages"] = {k: messages[k] for k in keep}
        self.state_path.write_text(
            json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def _record_broadcast(self, message_id: int, task: Task) -> None:
        if not task.conversation_id:
            return
        state = self._load_state()
        state.setdefault("messages", {})[str(message_id)] = {
            "conversation_id": task.conversation_id,
            "project_name": task.project_name,
        }
        state["last_conversation_id"] = task.conversation_id
        self._save_state(state)

    # ── Bot API 调用 ─────────────────────────────────────────

    async def _api(
        self,
        method: str,
        payload: Optional[dict] = None,
        files: Optional[dict] = None,
    ) -> dict:
        url = f"{_API_BASE}/bot{self.token}/{method}"
        client_kw: dict[str, Any] = {"timeout": 90.0}
        if self._transport is not None:
            client_kw["transport"] = self._transport
        elif self.proxy:
            client_kw["proxy"] = self.proxy
        async with httpx.AsyncClient(**client_kw) as client:
            if files:
                resp = await client.post(url, data=payload or {}, files=files)
            else:
                resp = await client.post(url, json=payload or {})
        if resp.status_code != 200:
            raise TelegramError(f"{method} 返回 HTTP {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
        if not data.get("ok"):
            raise TelegramError(f"{method} 失败: {data.get('description', '')}")
        return data.get("result", {})

    # ── 出向：任务播报 ───────────────────────────────────────

    def format_task_message(self, task: Task, excerpt: str) -> str:
        label = _STATUS_LABELS.get(task.status, str(task.status))
        lines = [f"{label} · {task.project_name or task.project}"]
        prompt = task.prompt.strip()
        lines.append(f"📝 {prompt[:120]}{'…' if len(prompt) > 120 else ''}")
        if excerpt:
            lines.append("")
            lines.append(excerpt)
        return "\n".join(lines)

    def _output_excerpt(self, task: Task, max_chars: int = 600) -> str:
        from coderfleet.server.digest import _read_output_excerpt
        return _read_output_excerpt(
            self.workspace_dir / "tasks", task.id, task.type, max_chars=max_chars
        )

    async def notify_task(self, task: Task) -> None:
        """任务终态播报入口。任何失败只记日志——播报绝不能影响任务收尾。"""
        if not self.is_configured() or self.notify_mode == "off":
            return
        if task.status not in _STATUS_LABELS:
            return
        try:
            excerpt = self._output_excerpt(task)
            text = self.format_task_message(task, excerpt)
            result = await self._api("sendMessage", {
                "chat_id": self.chat_ids[0],
                "text": text,
            })
            message_id = result.get("message_id")
            if message_id is not None:
                self._record_broadcast(int(message_id), task)
        except Exception as exc:
            logger.warning("Telegram 播报失败 [task=%s]: %s", task.id, exc)

    async def send_test_message(self) -> None:
        """连通性自检：向白名单第一个 chat 发一条测试消息。失败抛 TelegramError。"""
        if not self.is_configured():
            raise TelegramError("未配置 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID")
        await self._api("sendMessage", {
            "chat_id": self.chat_ids[0],
            "text": "🚀 CoderFleet Telegram 通知已连通",
        })
