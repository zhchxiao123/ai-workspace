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


# ── 语音播报 ────────────────────────────────────────────────

def _voice_ws(tmp_path):
    return _ws(tmp_path, telegram_bot_token="TOK", telegram_chat_id="123",
               telegram_notify_mode="voice")


def test_voice_notify_sends_ogg_with_caption(tmp_path):
    sent = {}

    def handler(req: httpx.Request) -> httpx.Response:
        if str(req.url).endswith("/sendVoice"):
            sent["content_type"] = req.headers.get("content-type", "")
            sent["body"] = req.content
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 55}})
        pytest.fail(f"意外请求: {req.url}")

    async def summarizer(text):
        return "任务顺利完成，十二个测试全部通过。"

    async def synth(text):
        sent["spoken"] = text
        return b"OGG_OPUS_BYTES"

    ws = _voice_ws(tmp_path)
    bridge = _bridge(ws, handler, summarizer=summarizer, voice_synth=synth)
    _run(bridge.notify_task(_task()))

    assert sent["spoken"] == "任务顺利完成，十二个测试全部通过。"
    assert "multipart/form-data" in sent["content_type"]
    assert b"OGG_OPUS_BYTES" in sent["body"]
    assert "myproj".encode() in sent["body"]          # caption 带项目名
    # 语音播报同样记录映射，可被 reply 续聊
    state = json.loads((ws / "telegram_state.json").read_text())
    assert state["messages"]["55"]["conversation_id"] == "c1"


def test_voice_notify_degrades_to_text_on_synth_failure(tmp_path):
    sent = {}

    def handler(req: httpx.Request) -> httpx.Response:
        if str(req.url).endswith("/sendMessage"):
            sent["text"] = json.loads(req.content)["text"]
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 9}})
        pytest.fail(f"应降级为 sendMessage: {req.url}")

    async def summarizer(text):
        return "摘要"

    async def broken_synth(text):
        raise RuntimeError("edge-tts unavailable")

    bridge = _bridge(_voice_ws(tmp_path), handler,
                     summarizer=summarizer, voice_synth=broken_synth)
    _run(bridge.notify_task(_task()))
    assert "myproj" in sent["text"]


def test_voice_notify_degrades_to_text_when_summarizer_unconfigured(tmp_path):
    sent = {}

    def handler(req: httpx.Request) -> httpx.Response:
        if str(req.url).endswith("/sendMessage"):
            sent["text"] = json.loads(req.content)["text"]
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 9}})
        pytest.fail(f"应降级为 sendMessage: {req.url}")

    async def broken_summarizer(text):
        raise RuntimeError("SYSTEM_LLM 未配置")

    async def synth(text):  # pragma: no cover
        pytest.fail("摘要失败后不应再走 TTS")

    bridge = _bridge(_voice_ws(tmp_path), handler,
                     summarizer=broken_summarizer, voice_synth=synth)
    _run(bridge.notify_task(_task()))
    assert "✅" in sent["text"]


def test_voice_summary_prompt_is_colloquial_chinese(tmp_path):
    from coderfleet.server.telegram_bridge import build_voice_summary_prompt

    system, user = build_voice_summary_prompt(_task(), "12 tests passed, login fixed.")
    assert "口语" in system
    assert "myproj" in user
    assert "12 tests passed" in user
    assert "fix the login bug" in user


def test_ffmpeg_opus_args():
    from coderfleet.server.telegram_bridge import ffmpeg_opus_args

    args = ffmpeg_opus_args("/tmp/in.mp3", "/tmp/out.ogg")
    assert args[0] == "ffmpeg"
    assert "/tmp/in.mp3" in args
    assert args[-1] == "/tmp/out.ogg"
    i = args.index("-c:a")
    assert args[i + 1] == "libopus"    # Telegram 语音消息硬性要求 OGG/Opus


# ── 入向：回复续聊 ──────────────────────────────────────────

class FakeScheduler:
    def __init__(self, fail: Exception | None = None, tasks: list | None = None):
        self.submitted: list[tuple[str, str]] = []
        self._fail = fail
        self._tasks = tasks or []

    def list_tasks(self):
        return self._tasks

    def conversation_queue_full(self, conversation_id):
        pending = sum(
            1 for t in self._tasks
            if t.conversation_id == conversation_id
            and t.status in (TaskStatus.pending, TaskStatus.scheduled)
        )
        return pending >= 3

    def get_conversation(self, conversation_id):
        from types import SimpleNamespace
        return SimpleNamespace(id=conversation_id, name="登录修复链")

    async def submit(self, prompt, conversation_id=None, **kw):
        if self._fail:
            raise self._fail
        self.submitted.append((prompt, conversation_id))
        return _task(id="t-new", conversation_id=conversation_id)


def _update(update_id=1, text="再跑一遍测试", chat_id=123, reply_to=None, voice=None):
    msg = {"message_id": 900 + update_id, "chat": {"id": chat_id}}
    if text is not None:
        msg["text"] = text
    if reply_to is not None:
        msg["reply_to_message"] = {"message_id": reply_to}
    if voice is not None:
        msg["voice"] = voice
    return {"update_id": update_id, "message": msg}


def _poll_setup(tmp_path, updates, fake=None, extra_routes=None):
    """返回 (bridge, sent, fake_scheduler)。handler 派发 getUpdates / sendMessage。"""
    sent: list[dict] = []
    seen = {"offset": None}

    def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        if url.endswith("/getUpdates"):
            seen["offset"] = json.loads(req.content).get("offset")
            return httpx.Response(200, json={"ok": True, "result": updates})
        if url.endswith("/sendMessage"):
            sent.append(json.loads(req.content))
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})
        if extra_routes:
            resp = extra_routes(req)
            if resp is not None:
                return resp
        return httpx.Response(404, json={"ok": False, "description": url})

    ws = _configured(tmp_path)
    bridge = _bridge(ws, handler)
    fake = fake or FakeScheduler()
    bridge.scheduler = fake
    return bridge, sent, fake, seen


def _seed_broadcast(ws, message_id=42, conversation_id="c1"):
    (ws / "telegram_state.json").write_text(json.dumps({
        "offset": 5,
        "messages": {str(message_id): {"conversation_id": conversation_id,
                                       "project_name": "myproj"}},
        "last_conversation_id": conversation_id,
    }), encoding="utf-8")


def test_reply_to_broadcast_continues_conversation(tmp_path):
    bridge, sent, fake, seen = _poll_setup(
        tmp_path, [_update(update_id=7, reply_to=42)])
    _seed_broadcast(bridge.workspace_dir)

    _run(bridge.poll_once())

    assert seen["offset"] == 5           # getUpdates 带上持久化 offset
    assert fake.submitted == [("再跑一遍测试", "c1")]
    assert len(sent) == 1                # 提交回执，含项目名与会话名
    assert "myproj" in sent[0]["text"]
    assert "登录修复链" in sent[0]["text"]
    state = json.loads((bridge.workspace_dir / "telegram_state.json").read_text())
    assert state["offset"] == 8          # update_id+1 已持久化


def test_bare_message_routes_to_last_conversation(tmp_path):
    bridge, sent, fake, _ = _poll_setup(tmp_path, [_update(update_id=9)])
    _seed_broadcast(bridge.workspace_dir, conversation_id="c-last")

    _run(bridge.poll_once())
    assert fake.submitted == [("再跑一遍测试", "c-last")]


def test_bare_message_without_history_gets_usage_hint(tmp_path):
    bridge, sent, fake, _ = _poll_setup(tmp_path, [_update(update_id=9)])

    _run(bridge.poll_once())
    assert fake.submitted == []
    assert len(sent) == 1
    assert "回复" in sent[0]["text"]     # 使用提示


def test_non_whitelisted_chat_is_dropped(tmp_path):
    bridge, sent, fake, _ = _poll_setup(
        tmp_path, [_update(update_id=9, chat_id=666)])
    _seed_broadcast(bridge.workspace_dir)

    _run(bridge.poll_once())
    assert fake.submitted == []
    assert sent == []
    # offset 仍然前进，不会反复消费同一条消息
    state = json.loads((bridge.workspace_dir / "telegram_state.json").read_text())
    assert state["offset"] == 10


def test_submit_failure_reported_to_user(tmp_path):
    bridge, sent, fake, _ = _poll_setup(
        tmp_path, [_update(update_id=9)],
        fake=FakeScheduler(fail=RuntimeError("没有匹配的可用账号")))
    _seed_broadcast(bridge.workspace_dir)

    _run(bridge.poll_once())
    assert "没有匹配的可用账号" in sent[0]["text"]


def test_conversation_queue_full_rejected(tmp_path):
    pending = [_task(id=f"p{i}", status=TaskStatus.pending, conversation_id="c1")
               for i in range(3)]
    bridge, sent, fake, _ = _poll_setup(
        tmp_path, [_update(update_id=9, reply_to=42)],
        fake=FakeScheduler(tasks=pending))
    _seed_broadcast(bridge.workspace_dir)

    _run(bridge.poll_once())
    assert fake.submitted == []
    assert "队列" in sent[0]["text"]


def test_poll_does_not_clobber_mappings_written_during_long_poll(tmp_path):
    """getUpdates 挂起期间 notify_task 落盘的映射，不能被轮询保存 offset 时覆盖。"""
    bridge_holder = {}

    def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        if url.endswith("/getUpdates"):
            # 模拟长轮询挂起期间有任务完成并落盘新映射
            bridge_holder["bridge"]._record_broadcast(77, _task(conversation_id="c-new"))
            return httpx.Response(200, json={"ok": True, "result": [
                _update(update_id=9, chat_id=666),  # 非白名单，仅推动 offset 保存
            ]})
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    ws = _configured(tmp_path)
    bridge = _bridge(ws, handler)
    bridge.scheduler = FakeScheduler()
    bridge_holder["bridge"] = bridge
    _seed_broadcast(ws)

    _run(bridge.poll_once())

    state = json.loads((ws / "telegram_state.json").read_text())
    assert state["offset"] == 10                     # offset 正常前进
    assert "77" in state["messages"]                 # 长轮询期间写入的映射仍在
    assert state["last_conversation_id"] == "c-new"


def test_reply_to_unmapped_message_warns_instead_of_misrouting(tmp_path):
    bridge, sent, fake, _ = _poll_setup(
        tmp_path, [_update(update_id=9, reply_to=999)])  # 999 不在映射中
    _seed_broadcast(bridge.workspace_dir, conversation_id="c-last")

    _run(bridge.poll_once())
    # 不能静默落到最近会话——用户以为自己定向了某条任务链
    assert fake.submitted == []
    assert len(sent) == 1
    assert "无法关联" in sent[0]["text"]


def test_broadcast_goes_to_all_whitelisted_chats(tmp_path):
    sent = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        sent.append(body["chat_id"])
        return httpx.Response(200, json={"ok": True,
                                         "result": {"message_id": 100 + len(sent)}})

    ws = _ws(tmp_path, telegram_bot_token="TOK", telegram_chat_id="123,456",
             telegram_notify_mode="text")
    _run(_bridge(ws, handler).notify_task(_task()))

    assert sent == ["123", "456"]
    state = json.loads((ws / "telegram_state.json").read_text())
    # 两条播报消息都可被 reply 续聊
    assert state["messages"]["101"]["conversation_id"] == "c1"
    assert state["messages"]["102"]["conversation_id"] == "c1"


# ── 语音输入（ASR 续聊） ────────────────────────────────────

def _asr_routes(audio=b"OGG_IN", transcription="把日志发我看看", status=200):
    def routes(req: httpx.Request):
        url = str(req.url)
        if url.endswith("/getFile"):
            return httpx.Response(200, json={"ok": True,
                                             "result": {"file_path": "voice/file_1.oga"}})
        if "/file/botTOK/" in url:
            return httpx.Response(200, content=audio)
        if url.endswith("/audio/transcriptions"):
            if status != 200:
                return httpx.Response(status, text="asr down")
            return httpx.Response(200, json={"text": transcription})
        return None
    return routes


def test_voice_reply_transcribed_and_submitted(tmp_path):
    bridge, sent, fake, _ = _poll_setup(
        tmp_path,
        [_update(update_id=9, text=None, reply_to=42, voice={"file_id": "F1"})],
        extra_routes=_asr_routes())
    _seed_broadcast(bridge.workspace_dir)
    _ws(tmp_path, telegram_asr_api_key="AK", telegram_asr_model="whisper-1")

    _run(bridge.poll_once())

    assert fake.submitted == [("把日志发我看看", "c1")]
    # 先回转写回执，再回提交回执
    assert "🎧" in sent[0]["text"] and "把日志发我看看" in sent[0]["text"]
    assert len(sent) == 2


def test_voice_message_without_asr_config_gets_hint(tmp_path):
    bridge, sent, fake, _ = _poll_setup(
        tmp_path,
        [_update(update_id=9, text=None, reply_to=42, voice={"file_id": "F1"})],
        extra_routes=_asr_routes())
    _seed_broadcast(bridge.workspace_dir)

    _run(bridge.poll_once())
    assert fake.submitted == []
    assert len(sent) == 1
    assert "TELEGRAM_ASR" in sent[0]["text"]     # 提示改用文本/配置 ASR


def test_voice_transcription_failure_reported(tmp_path):
    bridge, sent, fake, _ = _poll_setup(
        tmp_path,
        [_update(update_id=9, text=None, reply_to=42, voice={"file_id": "F1"})],
        extra_routes=_asr_routes(status=500))
    _seed_broadcast(bridge.workspace_dir)
    _ws(tmp_path, telegram_asr_api_key="AK")

    _run(bridge.poll_once())
    assert fake.submitted == []
    assert "转写失败" in sent[0]["text"]


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
