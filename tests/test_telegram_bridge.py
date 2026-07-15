"""Tests for the Telegram bridge — 任务播报与回复续聊。"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from coderfleet.config import set_config
from coderfleet.server.models import AccountType, Task, TaskStatus
from coderfleet.server.telegram_bridge import TelegramBridge


def _run(coro):
    return asyncio.run(coro)


def _ws(tmp_path, **cfg):
    for k, v in cfg.items():
        set_config(tmp_path, k.upper(), v)
    return tmp_path


def _bridge(ws, handler=None, **kw) -> TelegramBridge:
    transport = httpx.MockTransport(handler) if handler else None
    return TelegramBridge(ws, transport=transport, **kw)


# ── 配置 ────────────────────────────────────────────────────

def test_is_configured_requires_token_and_chat_id(tmp_path):
    assert not _bridge(tmp_path).is_configured()
    _ws(tmp_path, telegram_bot_token="TOK")
    assert not _bridge(tmp_path).is_configured()
    _ws(tmp_path, telegram_chat_id="123")
    assert _bridge(tmp_path).is_configured()


def test_config_changes_take_effect_without_restart(tmp_path):
    bridge = _bridge(tmp_path)
    assert not bridge.is_configured()
    _ws(tmp_path, telegram_bot_token="TOK", telegram_chat_id="123")
    assert bridge.is_configured()


# ── 文本播报 ────────────────────────────────────────────────

def _task(status=TaskStatus.done, **kw):
    fields = dict(
        id="t1", status=status, account="a1", type=AccountType.claude,
        prompt="fix the login bug", project="/repos/myproj",
        project_name="myproj", conversation_id="c1",
    )
    fields.update(kw)
    return Task(**fields)


def _configured(tmp_path, **extra):
    return _ws(tmp_path, telegram_bot_token="TOK", telegram_chat_id="123",
               telegram_notify_mode="text", **extra)


def test_notify_task_sends_text_and_records_mapping(tmp_path):
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"] = str(req.url)
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 42}})

    ws = _configured(tmp_path)
    bridge = _bridge(ws, handler)
    _run(bridge.notify_task(_task()))

    assert "/botTOK/sendMessage" in seen["url"]
    assert seen["body"]["chat_id"] == "123"
    text = seen["body"]["text"]
    assert "myproj" in text
    assert "✅" in text
    assert "fix the login bug" in text

    state = json.loads((ws / "telegram_state.json").read_text())
    assert state["messages"]["42"]["conversation_id"] == "c1"
    assert state["last_conversation_id"] == "c1"


def test_notify_task_includes_output_excerpt(tmp_path):
    ws = _configured(tmp_path)
    (ws / "tasks").mkdir(exist_ok=True)
    log_line = json.dumps({
        "type": "assistant",
        "message": {"content": [{"type": "text", "text": "全部 12 个测试通过，已修复登录问题。"}]},
    })
    (ws / "tasks" / "t1.log").write_text(log_line + "\n", encoding="utf-8")

    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    _run(_bridge(ws, handler).notify_task(_task()))
    assert "全部 12 个测试通过" in seen["body"]["text"]


def test_notify_task_noop_when_unconfigured_or_off(tmp_path):
    def handler(req: httpx.Request) -> httpx.Response:  # pragma: no cover
        pytest.fail("未配置时不应发起任何 HTTP 请求")

    # 完全未配置
    _run(_bridge(tmp_path, handler).notify_task(_task()))
    # 配置齐全但 notify_mode=off
    _ws(tmp_path, telegram_bot_token="TOK", telegram_chat_id="123",
        telegram_notify_mode="off")
    _run(_bridge(tmp_path, handler).notify_task(_task()))
    # 非终态任务不播报
    _configured(tmp_path)
    _run(_bridge(tmp_path, handler).notify_task(_task(status=TaskStatus.running)))


def test_notify_task_survives_api_failure(tmp_path):
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    ws = _configured(tmp_path)
    # 不抛异常——播报失败不能影响任务收尾
    _run(_bridge(ws, handler).notify_task(_task()))


# ── 服务端点（与 CLI 能力对等） ──────────────────────────────

def test_endpoint_telegram_status_and_test(tmp_path, monkeypatch):
    from fastapi import HTTPException
    from coderfleet.server import main as server_main

    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"] = str(req.url)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 7}})

    # 未配置：status 反映 configured=False，test 返回 400
    monkeypatch.setattr(server_main, "telegram_bridge", _bridge(tmp_path, handler))
    assert _run(server_main.telegram_status())["configured"] is False
    with pytest.raises(HTTPException) as exc:
        _run(server_main.telegram_test())
    assert exc.value.status_code == 400

    # 配置后：status 反映模式，test 发送成功
    _configured(tmp_path)
    status = _run(server_main.telegram_status())
    assert status["configured"] is True
    assert status["notify_mode"] == "text"
    _run(server_main.telegram_test())
    assert "sendMessage" in seen["url"]


# ── CLI: coderfleet telegram test ───────────────────────────

def test_cli_telegram_test_unconfigured_fails_with_hint(tmp_path):
    from click.testing import CliRunner
    from coderfleet.telegram_cmds import telegram_group

    result = CliRunner().invoke(
        telegram_group, ["test"], obj={"workspace": tmp_path}
    )
    assert result.exit_code != 0
    assert "TELEGRAM_BOT_TOKEN" in result.output


def test_cli_telegram_test_sends_message(tmp_path, monkeypatch):
    from click.testing import CliRunner
    from coderfleet import telegram_cmds

    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"] = str(req.url)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 7}})

    ws = _configured(tmp_path)
    monkeypatch.setattr(
        telegram_cmds, "TelegramBridge",
        lambda w: TelegramBridge(w, transport=httpx.MockTransport(handler)),
    )
    result = CliRunner().invoke(
        telegram_cmds.telegram_group, ["test"], obj={"workspace": ws}
    )
    assert result.exit_code == 0, result.output
    assert "sendMessage" in seen["url"]
    assert "✓" in result.output
