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

import asyncio
import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, NamedTuple, Optional

import httpx

from coderfleet.config import load_config
from coderfleet.server.models import TASK_STATUS_LABELS, Task, TaskStatus

logger = logging.getLogger(__name__)

_API_BASE = "https://api.telegram.org"

# 映射条目超过上限后按插入序淘汰——只需要覆盖"最近可回复"的窗口
_MAX_MESSAGE_MAPPINGS = 200

# /chats 列表长度上限——Telegram 消息要一眼能读完
_MAX_CHAT_LIST = 10

# Telegram 单条消息字符硬上限
_TELEGRAM_MESSAGE_LIMIT = 4096

# 两步式 /new（按钮选项目 → 下一条消息作开场指令）的等待窗口（秒）
_PENDING_NEW_TTL = 600

# setMyCommands 注册的命令菜单（客户端打 / 弹出）
_BOT_COMMANDS = [
    {"command": "chats",    "description": "最近会话列表"},
    {"command": "use",      "description": "切换普通消息的默认会话"},
    {"command": "projects", "description": "项目列表"},
    {"command": "new",      "description": "在项目上开新会话链"},
    {"command": "status",   "description": "运行中与排队的任务"},
    {"command": "id",       "description": "显示当前 chat id（绑定话题群用）"},
    {"command": "help",     "description": "命令帮助"},
]

# bot 命令集。未命中的 "/xxx" 一律按任务指令放行（slash-skill 调用不受影响）。
# 匹配 "/cmd" 或群聊里的 "/cmd@botname"，参数可跨行。
_COMMAND_RE = re.compile(r"^/([a-z]+)(?:@\w+)?(?:\s+(.*))?$", re.DOTALL)

_HELP_TEXT = (
    "CoderFleet bot 命令：\n"
    "/chats — 最近会话列表\n"
    "/use <编号> — 把普通消息的默认路由切到某条会话\n"
    "/projects — 项目列表\n"
    "/new <项目> <指令> — 在指定项目开新会话链\n"
    "/status — 运行中与排队的任务\n"
    "/id — 显示当前 chat id（绑定话题群用）\n"
    "/help — 本帮助\n"
    "\n"
    "回复任一任务播报可直接续聊对应任务链（优先级高于默认路由）。"
)

_COLD_START_HINT = (
    "还没有可续聊的会话。回复任一任务播报即可续聊；"
    "或发送 /chats 查看会话列表后用 /use 选择；"
    "也可以用 /new <项目> <指令> 直接开一条新会话链。"
)


class TelegramError(RuntimeError):
    """Telegram Bot API 调用失败（未配置、网络错误、上游返回非 ok 等）。"""


class _MsgCtx(NamedTuple):
    """一条入向消息的路由上下文：chat + 话题 + 话题所属项目。"""
    chat_id: str
    thread_id: Optional[int] = None
    project: str = ""  # 话题所属项目；私聊 / General / 未知话题为 ""

    @property
    def key(self) -> str:
        """per-chat 状态键：话题内为 chat:thread 复合键，各话题上下文互不串扰；
        私聊 / General 保持纯 chat_id（与旧状态文件兼容）。"""
        return f"{self.chat_id}:{self.thread_id}" if self.thread_id else self.chat_id


def build_voice_summary_prompt(task, output_text: str) -> tuple[str, str]:
    """组装语音摘要的 (system, user) 提示词。纯函数，独立可测。"""
    system = (
        "你是任务播报员。把编码任务的执行结果压缩成 2~3 句口语化中文，"
        "适合语音播报：不出现代码、路径、标点堆砌，先说结论再说要点。"
    )
    label = TASK_STATUS_LABELS.get(task.status, str(task.status))
    user = (
        f"项目：{task.project_name or task.project}\n"
        f"状态：{label}\n"
        f"任务指令：{task.prompt}\n"
        f"执行结果：\n{output_text or '（无输出）'}"
    )
    return system, user


def ffmpeg_opus_args(src: str, dst: str) -> list[str]:
    """TTS 中间产物 → Telegram 语音消息要求的 OGG/Opus。纯函数，独立可测。"""
    return [
        "ffmpeg", "-y", "-i", src,
        "-c:a", "libopus", "-b:a", "32k", "-ar", "48000", "-ac", "1",
        dst,
    ]


class TelegramBridge:
    def __init__(
        self,
        workspace_dir: Path,
        *,
        transport: Optional[httpx.BaseTransport] = None,
        summarizer: Optional[Callable[[str], Awaitable[str]]] = None,
        voice_synth: Optional[Callable[[str], Awaitable[bytes]]] = None,
    ) -> None:
        self.workspace_dir = workspace_dir
        self.state_path = workspace_dir / "telegram_state.json"
        # 续聊提交入口，由 main.py 注入 Scheduler；测试注入 fake
        self.scheduler = None
        # 仅供测试注入 httpx.MockTransport；真实路径永远为 None。
        self._transport = transport
        # 语音链路 seam：async callable(text) -> str / ogg bytes；默认实现见下
        self._summarizer = summarizer
        self._voice_synth = voice_synth
        self._poll_task: Optional[asyncio.Task] = None
        self._commands_registered = False
        self._commands: dict[str, Callable[[str, str], Awaitable[None]]] = {
            "chats":    self._cmd_chats,
            "use":      self._cmd_use,
            "projects": self._cmd_projects,
            "new":      self._cmd_new,
            "status":   self._cmd_status,
            "id":       self._cmd_id,
            "help":     self._cmd_help,
        }

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

    @property
    def topic_group_id(self) -> str:
        """Topics 论坛群 id；配置后播报按项目话题分频道发进该群。"""
        return self._cfg().get("TELEGRAM_TOPIC_GROUP_ID", "").strip()

    @property
    def asr_api_key(self) -> str:
        return self._cfg().get("TELEGRAM_ASR_API_KEY", "").strip()

    @property
    def asr_base_url(self) -> str:
        url = self._cfg().get("TELEGRAM_ASR_BASE_URL", "").strip()
        return (url or "https://api.openai.com/v1").rstrip("/")

    @property
    def asr_model(self) -> str:
        return self._cfg().get("TELEGRAM_ASR_MODEL", "").strip() or "whisper-1"

    def is_configured(self) -> bool:
        """未配置时所有入口应优雅降级为 no-op，而不是抛错。

        私聊白名单与话题群二者有其一即可用——只配话题群（群成员资格
        即信任边界）不应要求再配一个用不上的私聊白名单。
        """
        return bool(self.token and (self.chat_ids or self.topic_group_id))

    def asr_configured(self) -> bool:
        return bool(self.asr_api_key)

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

    def _update_state(self, mutate) -> dict[str, Any]:
        """同步读改写：块内无 await，事件循环下天然原子，不会互相覆盖。"""
        state = self._load_state()
        mutate(state)
        self._save_state(state)
        return state

    def _set_chat_entry(self, section: str, chat_id: str, value: Any) -> None:
        """写入 per-chat 状态区（defaults / chat_lists / project_lists / pending_new）。"""
        self._update_state(lambda s: s.setdefault(section, {}).update({chat_id: value}))

    @staticmethod
    def _chat_id_of(container: dict) -> str:
        """从 message / callback_query.message 中取 chat id。"""
        return str((container or {}).get("chat", {}).get("id", ""))

    def _is_trusted_chat(self, chat_id: str) -> bool:
        """私聊走白名单；配置了话题群则群成员资格即信任边界。"""
        if chat_id in self.chat_ids:
            return True
        group = self.topic_group_id
        return bool(group) and chat_id == group

    def _msg_ctx(self, msg: dict) -> _MsgCtx:
        """从消息构造路由上下文；话题 thread → 项目由话题注册表反查。"""
        chat_id = self._chat_id_of(msg)
        thread_id = msg.get("message_thread_id")
        project = ""
        if thread_id and self.topic_group_id and chat_id == self.topic_group_id:
            topics = self._load_state().get("topics", {})
            project = next(
                (p for p, t in topics.items() if int(t) == int(thread_id)), "")
        return _MsgCtx(chat_id, thread_id, project)

    async def _send_text(self, ctx: _MsgCtx, text: str, **extra: Any) -> dict:
        """向消息来源处回话；话题消息的回执落在同一话题。"""
        payload: dict[str, Any] = {"chat_id": ctx.chat_id, "text": text, **extra}
        if ctx.thread_id:
            payload["message_thread_id"] = ctx.thread_id
        return await self._api("sendMessage", payload)

    def _record_broadcast(self, message_id: int, task: Task) -> None:
        if not task.conversation_id:
            return

        def mutate(state):
            state.setdefault("messages", {})[str(message_id)] = {
                "conversation_id": task.conversation_id,
                "project_name": task.project_name,
            }
            state["last_conversation_id"] = task.conversation_id

        self._update_state(mutate)

    # ── Bot API 调用 ─────────────────────────────────────────

    def _client_kwargs(self, timeout: float) -> dict[str, Any]:
        kw: dict[str, Any] = {"timeout": timeout}
        if self._transport is not None:
            kw["transport"] = self._transport
        elif self.proxy:
            kw["proxy"] = self.proxy
        return kw

    async def _api(
        self,
        method: str,
        payload: Optional[dict] = None,
        files: Optional[dict] = None,
    ) -> dict:
        url = f"{_API_BASE}/bot{self.token}/{method}"
        # getUpdates 每 25s 一轮，INFO 会刷屏，降为 DEBUG；其余调用全量记录
        level = logging.DEBUG if method == "getUpdates" else logging.INFO
        logger.log(level, "Bot API %s 开始 (proxy=%s)", method, self.proxy or "直连")
        t0 = time.monotonic()
        try:
            async with httpx.AsyncClient(**self._client_kwargs(90.0)) as client:
                if files:
                    resp = await client.post(url, data=payload or {}, files=files)
                else:
                    resp = await client.post(url, json=payload or {})
        except Exception as exc:
            logger.warning("Bot API %s 网络失败 (%.1fs, proxy=%s): %r",
                           method, time.monotonic() - t0, self.proxy or "直连", exc)
            raise
        if resp.status_code != 200:
            raise TelegramError(f"{method} 返回 HTTP {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
        if not data.get("ok"):
            raise TelegramError(f"{method} 失败: {data.get('description', '')}")
        logger.log(level, "Bot API %s 完成 (%.1fs)", method, time.monotonic() - t0)
        return data.get("result", {})

    # ── 出向：任务播报 ───────────────────────────────────────

    def format_task_message(self, task: Task, excerpt: str) -> str:
        label = TASK_STATUS_LABELS.get(task.status, str(task.status))
        lines = [f"{label} · {task.project_name or task.project}"]
        prompt = task.prompt.strip()
        lines.append(f"📝 {prompt[:200]}{'…' if len(prompt) > 200 else ''}")
        if excerpt:
            lines.append("")
            lines.append(excerpt)
        text = "\n".join(lines)
        # Telegram 单条消息硬上限 4096
        return text[:_TELEGRAM_MESSAGE_LIMIT - 1] + "…" \
            if len(text) > _TELEGRAM_MESSAGE_LIMIT else text

    def _output_excerpt(self, task: Task, max_chars: int = 3000) -> str:
        """任务日志 → 播报正文。与 Digest 的摘录不同：保留代码块——
        编码任务的产出经常大半是代码，剥掉等于没播报。"""
        log_path = self.workspace_dir / "tasks" / f"{task.id}.log"
        if not log_path.exists():
            return ""
        try:
            from coderfleet.server.log_parser import parse_log
            raw = log_path.read_text(encoding="utf-8", errors="replace")
            text = parse_log(raw, task.type).text.strip()
            text = re.sub(r"\n{3,}", "\n\n", text)
            if len(text) > max_chars:
                text = text[:max_chars] + "…"
            return text
        except Exception:
            return ""

    async def notify_task(self, task: Task) -> None:
        """任务终态播报入口，发往全部白名单 chat。任何失败只记日志——播报绝不能影响任务收尾。"""
        if not self.is_configured():
            logger.info("跳过 Telegram 播报 [task=%s]：未配置 token/chat_id (workspace=%s)",
                        task.id, self.workspace_dir)
            return
        if self.notify_mode == "off":
            logger.info("跳过 Telegram 播报 [task=%s]：TELEGRAM_NOTIFY_MODE=off", task.id)
            return
        if task.status not in TASK_STATUS_LABELS:
            return
        logger.info("Telegram 播报开始 [task=%s status=%s mode=%s chats=%d]",
                    task.id, task.status.value, self.notify_mode, len(self.chat_ids))
        try:
            excerpt = self._output_excerpt(task)
            ogg: Optional[bytes] = None
            if self.notify_mode == "voice":
                try:
                    summary = await self._summarize(task, excerpt)
                    ogg = await self._synthesize(summary)
                except Exception as exc:
                    # 语音链路（摘要/TTS/ffmpeg）任一环节不可用 → 降级为文本
                    logger.warning("Telegram 语音播报失败，降级为文本 [task=%s]: %s",
                                   task.id, exc)
            label = TASK_STATUS_LABELS.get(task.status, "")
            text = self.format_task_message(task, excerpt)

            async def send_one(chat_id: str, thread_id: Optional[int]) -> dict:
                extra = {"message_thread_id": thread_id} if thread_id else {}
                if ogg is not None:
                    return await self._api(
                        "sendVoice",
                        {"chat_id": chat_id,
                         "caption": f"{label} · {task.project_name or task.project}",
                         **extra},
                        files={"voice": ("result.ogg", ogg, "audio/ogg")},
                    )
                return await self._api(
                    "sendMessage", {"chat_id": chat_id, "text": text, **extra})

            for chat_id, thread_id in await self._broadcast_targets(task):
                try:
                    result = await send_one(chat_id, thread_id)
                except TelegramError as exc:
                    if not thread_id:
                        raise
                    # 话题可能已被手动删除：清坏注册、降级 General，播报绝不丢
                    logger.warning(
                        "话题发送失败，清除注册并降级 General [project=%s thread=%s]: %r",
                        task.project_name, thread_id, exc)
                    self._update_state(
                        lambda s: s.get("topics", {}).pop(task.project_name, None))
                    result = await send_one(chat_id, None)
                message_id = result.get("message_id")
                if message_id is not None:
                    self._record_broadcast(int(message_id), task)
                logger.info("Telegram 播报已送达 [task=%s chat=%s thread=%s message_id=%s voice=%s]",
                            task.id, chat_id, thread_id, message_id, ogg is not None)
        except Exception as exc:
            logger.warning("Telegram 播报失败 [task=%s]: %r", task.id, exc)

    async def _broadcast_targets(self, task: Task) -> list[tuple[str, Optional[int]]]:
        """播报目的地：配置话题群则只发群（按项目话题），否则发全部白名单私聊。"""
        group = self.topic_group_id
        if not group:
            return [(c, None) for c in self.chat_ids]
        thread_id = await self._ensure_topic(task.project_name)
        return [(group, thread_id)]

    async def _ensure_topic(self, project_name: str) -> Optional[int]:
        """项目 → 话题 thread id，首次懒创建并落盘。失败降级 General（返回 None）。"""
        if not project_name:
            return None
        state = self._load_state()
        existing = state.get("topics", {}).get(project_name)
        if existing:
            return int(existing)
        try:
            result = await self._api("createForumTopic", {
                "chat_id": self.topic_group_id, "name": project_name,
            })
            thread_id = int(result["message_thread_id"])
        except Exception as exc:
            logger.warning("话题创建失败，播报降级到 General [project=%s]: %r",
                           project_name, exc)
            await self._hint_topic_permission_once()
            return None
        # createForumTopic 挂起期间另一个播报可能已注册同项目的话题——
        # 注册表必须唯一，先注册者赢，避免播报散落在重复话题里
        registered: dict[str, int] = {}

        def mutate(state):
            topics = state.setdefault("topics", {})
            if project_name in topics:
                registered["thread"] = int(topics[project_name])
            else:
                topics[project_name] = thread_id
                registered["thread"] = thread_id

        self._update_state(mutate)
        if registered["thread"] != thread_id:
            logger.info("话题并发创建：采用已注册 thread [project=%s keep=%s drop=%s]",
                        project_name, registered["thread"], thread_id)
        else:
            logger.info("已为项目创建话题 [project=%s thread=%s]",
                        project_name, thread_id)
        return registered["thread"]

    async def _hint_topic_permission_once(self) -> None:
        """群权限缺失的一次性提示；发送成功后才消耗"一次性"资格。"""
        if self._load_state().get("topic_hint_sent"):
            return
        try:
            await self._api("sendMessage", {
                "chat_id": self.topic_group_id,
                "text": "⚠️ 无法创建项目话题：请把 bot 设为群管理员并开启「管理话题」权限。"
                        "在此之前播报会发到 General。",
            })
        except Exception as exc:
            logger.warning("话题权限提示发送失败，下次播报重试: %r", exc)
            return
        self._update_state(lambda s: s.update(topic_hint_sent=True))

    async def _summarize(self, task: Task, excerpt: str) -> str:
        """结果 → 口语化中文摘要。system_llm 未配置时抛错，由上层降级为文本。"""
        if self._summarizer is not None:
            return await self._summarizer(excerpt)
        from coderfleet.server.system_llm import SystemLLM
        llm = SystemLLM.from_config(self.workspace_dir)
        if not llm.is_configured():
            raise TelegramError("SYSTEM_LLM 未配置，无法生成语音摘要")
        system, user = build_voice_summary_prompt(task, excerpt)
        return (await llm.complete(
            [{"role": "user", "content": user}], system=system, max_tokens=300,
        )).strip()

    async def _synthesize(self, text: str) -> bytes:
        """文本 → OGG/Opus 音频。edge-tts / ffmpeg 缺失时抛错，由上层降级。"""
        if self._voice_synth is not None:
            return await self._voice_synth(text)
        import subprocess
        import tempfile
        import edge_tts  # 缺失则 ImportError → 降级
        with tempfile.TemporaryDirectory() as tmp:
            mp3 = f"{tmp}/tts.mp3"
            ogg = f"{tmp}/tts.ogg"
            await edge_tts.Communicate(text, voice="zh-CN-XiaoxiaoNeural").save(mp3)
            proc = await asyncio.to_thread(
                subprocess.run, ffmpeg_opus_args(mp3, ogg),
                capture_output=True, timeout=60,
            )
            if proc.returncode != 0:
                raise TelegramError(f"ffmpeg 转码失败: {proc.stderr.decode()[:200]}")
            return Path(ogg).read_bytes()

    async def send_test_message(self) -> None:
        """连通性自检：向白名单第一个 chat 发一条测试消息。失败抛 TelegramError。"""
        if not self.is_configured():
            raise TelegramError("未配置 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID")
        await self._api("sendMessage", {
            "chat_id": self.chat_ids[0],
            "text": "🚀 CoderFleet Telegram 通知已连通",
        })

    # ── 入向：长轮询 + 回复续聊 ──────────────────────────────

    # 长轮询挂起秒数（Bot API 侧），客户端超时须大于它。
    # 不用 Telegram 允许的上限 50s：长轮询挂起期间无字节流动，
    # 常见代理链会把 30s+ 的空闲隧道掐断，导致每轮都 ReadTimeout。
    poll_timeout = 25

    async def poll_once(self) -> int:
        """执行一轮 getUpdates 并处理全部消息，返回消费的 update 数。"""
        if not self.is_configured():
            return 0
        await self._ensure_commands_registered()
        offset = self._load_state().get("offset", 0)
        updates = await self._api("getUpdates", {
            "offset": offset,
            "timeout": self.poll_timeout,
            "allowed_updates": ["message", "callback_query"],
        })
        for update in updates:
            # 无论处理成败都前进 offset——坏消息不能卡死队列
            self._advance_offset(update["update_id"] + 1)
            try:
                await self._handle_update(update)
            except Exception as exc:
                logger.warning("Telegram 消息处理失败 [update=%s]: %s",
                               update.get("update_id"), exc)
        return len(updates)

    def _advance_offset(self, new_offset: int) -> None:
        # 同步读改写：长轮询挂起期间 notify_task 可能已落盘新映射，
        # 不能拿轮询开始时的快照回写覆盖它
        state = self._load_state()
        if new_offset > state.get("offset", 0):
            state["offset"] = new_offset
            self._save_state(state)

    async def _ensure_commands_registered(self) -> None:
        """向 Telegram 注册命令菜单，进程内成功一次即可。失败下轮重试。"""
        if self._commands_registered:
            return
        try:
            await self._api("setMyCommands", {"commands": _BOT_COMMANDS})
            self._commands_registered = True
            logger.info("Telegram 命令菜单已注册（%d 个命令）", len(_BOT_COMMANDS))
        except Exception as exc:
            logger.warning("命令菜单注册失败，下轮重试: %r", exc)

    async def _handle_update(self, update: dict) -> None:
        callback = update.get("callback_query")
        if callback:
            await self._handle_callback(callback)
            return
        msg = update.get("message") or {}
        chat_id = self._chat_id_of(msg)
        text_raw = (msg.get("text") or "").strip()
        parsed = self._parse_command(text_raw)
        if parsed is not None and parsed[0] == "id":
            # /id 白名单豁免：绑定话题群前用户必须能先拿到群 id；只回显 id，不泄露其他信息
            await self._cmd_id(self._msg_ctx(msg), parsed[1])
            return
        if not self._is_trusted_chat(chat_id):
            logger.info("丢弃非白名单消息 [chat=%s]", chat_id or "?")
            return
        ctx = self._msg_ctx(msg)
        # Telegram 话题内所有消息技术上都 reply 话题首条消息（message_id == thread_id），
        # 剥掉这种隐式 reply，只保留真正指向播报的
        reply = msg.get("reply_to_message") or {}
        if ctx.thread_id and reply.get("message_id") == ctx.thread_id:
            msg = {k: v for k, v in msg.items() if k != "reply_to_message"}
            reply = {}
        voice = msg.get("voice")
        logger.info("收到 Telegram 消息 [chat=%s thread=%s project=%s reply_to=%s voice=%s]",
                    ctx.chat_id, ctx.thread_id, ctx.project or "-",
                    reply.get("message_id"), bool(voice))
        if voice:
            await self._handle_voice_message(ctx, voice, msg)
            return
        text = (msg.get("text") or "").strip()
        if not text:
            return
        command = self._parse_command(text)
        if command is not None:
            cmd, args = command
            logger.info("执行 bot 命令 [chat=%s cmd=/%s]", ctx.chat_id, cmd)
            await self._handle_command(ctx, cmd, args)
            return
        pending_project = self._take_pending_new(ctx.key)
        if pending_project:
            # 两步式 /new：这条消息是新会话的开场指令
            await self._create_new_session(ctx, pending_project, text)
            return
        await self._submit_continuation(ctx, text, msg)

    # ── 内联按钮回调 ─────────────────────────────────────────

    async def _handle_callback(self, callback: dict) -> None:
        msg = callback.get("message") or {}
        chat_id = self._chat_id_of(msg)
        if not self._is_trusted_chat(chat_id):
            logger.info("丢弃非白名单按钮回调 [chat=%s]", chat_id or "?")
            return
        ctx = self._msg_ctx(msg)
        data = callback.get("data", "")
        message_id = msg.get("message_id")
        logger.info("收到按钮回调 [chat=%s thread=%s data=%s]",
                    ctx.chat_id, ctx.thread_id, data)
        try:
            await self._api("answerCallbackQuery",
                            {"callback_query_id": callback.get("id", "")})
        except Exception as exc:
            logger.warning("answerCallbackQuery 失败: %r", exc)
        action, _, ident = data.partition(":")
        if action == "use" and ident:
            await self._callback_use(ctx, ident, message_id)
        elif action == "newp" and ident:
            await self._callback_new_project(ctx, ident, message_id)

    async def _edit_message(self, chat_id: str, message_id, text: str) -> None:
        """把按钮消息改写为选择结果——按钮随之消失，防止过期误触。"""
        if message_id is None:
            return
        try:
            await self._api("editMessageText", {
                "chat_id": chat_id, "message_id": message_id, "text": text,
            })
        except Exception as exc:
            logger.warning("editMessageText 失败: %r", exc)

    async def _switch_default(self, ctx: _MsgCtx, conv_id: str) -> Optional[str]:
        """校验会话仍存在后切默认路由。返回展示标签；会话不存在返回 None。

        过期按钮/编号可能指向已删除的会话——不校验就切，会装入一个
        每条消息都提交失败的死默认。
        """
        conv = None
        if self.scheduler is not None:
            try:
                conv = self.scheduler.get_conversation(conv_id)
            except Exception:
                conv = None
        if conv is None:
            return None
        self._set_chat_entry("defaults", ctx.key, conv_id)
        project = getattr(conv, "project_name", "") or ""
        return f"{project}「{conv.name}」" if project else f"「{conv.name}」"

    async def _callback_use(self, ctx: _MsgCtx, conv_id: str, message_id) -> None:
        label = await self._switch_default(ctx, conv_id)
        if label is None:
            await self._edit_message(
                ctx.chat_id, message_id,
                "该会话已不存在，请重新发送 /chats 获取最新列表。",
            )
            return
        await self._edit_message(
            ctx.chat_id, message_id,
            f"✅ 默认会话已切到 {label}，之后的普通消息都会续聊这里。",
        )

    async def _callback_new_project(self, ctx: _MsgCtx, project: str, message_id) -> None:
        expires = time.time() + _PENDING_NEW_TTL
        self._set_chat_entry("pending_new", ctx.key,
                             {"project": project, "expires_at": expires})
        await self._edit_message(
            ctx.chat_id, message_id,
            f"已选择项目 {project}。请发送新会话的首条指令（{_PENDING_NEW_TTL // 60} 分钟内有效）。",
        )

    def _take_pending_new(self, ctx_key: str) -> str:
        """取出并清除该上下文的两步式 /new 待办；过期返回空。"""
        taken: dict = {}

        def mutate(state):
            entry = state.get("pending_new", {}).pop(ctx_key, None)
            if entry:
                taken.update(entry)

        self._update_state(mutate)
        if not taken:
            return ""
        if float(taken.get("expires_at", 0)) < time.time():
            logger.info("两步式 /new 已过期 [ctx=%s project=%s]",
                        ctx_key, taken.get("project"))
            return ""
        return taken.get("project", "")

    # ── Bot 命令 ─────────────────────────────────────────────

    def _parse_command(self, text: str) -> Optional[tuple[str, str]]:
        """命中命令集返回 (命令, 参数)；否则 None（按任务指令放行）。"""
        m = _COMMAND_RE.match(text)
        if not m:
            return None
        name = m.group(1).lower()
        if name not in self._commands:
            return None
        return name, (m.group(2) or "").strip()

    async def _handle_command(self, ctx: _MsgCtx, cmd: str, args: str) -> None:
        await self._commands[cmd](ctx, args)

    async def _cmd_help(self, ctx: _MsgCtx, args: str) -> None:
        await self._send_text(ctx, _HELP_TEXT)

    async def _cmd_id(self, ctx: _MsgCtx, args: str) -> None:
        # 实际入口在 _handle_update 的白名单豁免路径；留在命令表供 /help 与命令菜单展示
        await self._send_text(ctx, f"当前 chat id：{ctx.chat_id}")

    def _recent_conversations(self, project: str = "") -> list:
        convs = list(self.scheduler.list_conversations()) if self.scheduler else []
        if project:
            convs = [c for c in convs
                     if (getattr(c, "project_name", "") or "") == project]
        convs.sort(key=lambda c: getattr(c, "updated", ""), reverse=True)
        return convs[:_MAX_CHAT_LIST]

    def _last_task_status(self, conversation) -> str:
        task_id = getattr(conversation, "last_task_id", "")
        if not task_id or self.scheduler is None:
            return ""
        task = self.scheduler.get_task(task_id)
        return task.status.value if task else ""

    async def _cmd_chats(self, ctx: _MsgCtx, args: str) -> None:
        convs = self._recent_conversations(project=ctx.project)
        if not convs:
            await self._send_text(
                ctx, f"{ctx.project} 还没有任何会话。" if ctx.project else "还没有任何会话。")
            return
        lines = ["最近会话："]
        numbering: dict[str, str] = {}
        keyboard: list[list[dict]] = []
        for i, c in enumerate(convs, start=1):
            numbering[str(i)] = c.id
            status = self._last_task_status(c)
            lines.append(
                f"{i}. {getattr(c, 'project_name', '') or '?'} · {c.name}"
                + (f" [{status}]" if status else "")
            )
            keyboard.append([{"text": f"切到 {i}·{c.name[:12]}",
                              "callback_data": f"use:{c.id}"}])
        lines.append("")
        lines.append("点按钮或发送 /use <编号> 把普通消息切到对应会话。")
        self._set_chat_entry("chat_lists", ctx.key, numbering)
        await self._send_text(ctx, "\n".join(lines),
                              reply_markup={"inline_keyboard": keyboard})

    async def _cmd_use(self, ctx: _MsgCtx, args: str) -> None:
        numbering = self._load_state().get("chat_lists", {}).get(ctx.key, {})
        conv_id = numbering.get(args.strip())
        if not conv_id:
            await self._send_text(
                ctx, "编号无效。先发送 /chats 获取最新会话列表，再 /use <编号>。")
            return
        label = await self._switch_default(ctx, conv_id)
        if label is None:
            await self._send_text(
                ctx, "该会话已不存在，请重新发送 /chats 获取最新列表。")
            return
        await self._send_text(
            ctx, f"✅ 默认会话已切到 {label}，之后的普通消息都会续聊这里。")

    async def _projects_or_hint(self, ctx: _MsgCtx) -> list:
        """项目列表；为空时直接回提示并返回 []。"""
        projects = list(self.scheduler.get_projects()) if self.scheduler else []
        if not projects:
            await self._send_text(ctx, "还没有配置任何项目。")
        return projects

    async def _cmd_projects(self, ctx: _MsgCtx, args: str) -> None:
        projects = await self._projects_or_hint(ctx)
        if not projects:
            return
        lines = ["项目列表："]
        numbering: dict[str, str] = {}
        for i, p in enumerate(projects, start=1):
            numbering[str(i)] = p.name
            lines.append(f"{i}. {p.name} · {p.account}")
        lines.append("")
        lines.append("发送 /new <项目名或编号> <指令> 开新会话链。")
        self._set_chat_entry("project_lists", ctx.key, numbering)
        await self._send_text(ctx, "\n".join(lines))

    async def _cmd_new(self, ctx: _MsgCtx, args: str) -> None:
        args = args.strip()
        if ctx.project:
            # 项目话题内：话题即项目上下文，整个参数就是指令
            if not args:
                await self._send_text(ctx, f"用法：/new <指令>（在 {ctx.project} 上开新会话链）。")
                return
            await self._create_new_session(ctx, ctx.project, args)
            return
        if not args:
            await self._offer_project_buttons(ctx)
            return
        parts = args.split(None, 1)
        if len(parts) < 2:
            await self._send_text(
                ctx, "用法：/new <项目名或编号> <指令>；或直接发 /new 用按钮选项目。")
            return
        project_ref, prompt = parts[0], parts[1].strip()
        project_name = self._resolve_project(ctx, project_ref)
        if project_name is None:
            names = "、".join(p.name for p in self.scheduler.get_projects()) \
                if self.scheduler else ""
            await self._send_text(
                ctx, f"项目「{project_ref}」不存在。现有项目：{names or '（无）'}")
            return
        await self._create_new_session(ctx, project_name, prompt)

    async def _offer_project_buttons(self, ctx: _MsgCtx) -> None:
        projects = await self._projects_or_hint(ctx)
        if not projects:
            return
        keyboard = [[{"text": p.name, "callback_data": f"newp:{p.name}"}]
                    for p in projects]
        await self._send_text(ctx, "选择要开新会话的项目：",
                              reply_markup={"inline_keyboard": keyboard})

    async def _create_new_session(self, ctx: _MsgCtx, project_name: str, prompt: str) -> None:
        try:
            task = await self.scheduler.submit(
                prompt=prompt,
                project_name=project_name,
                conversation_name=prompt[:32],
            )
            logger.info("新会话已创建 [project=%s conversation=%s task=%s]",
                        project_name, task.conversation_id, task.id)
        except Exception as exc:
            logger.warning("新会话创建失败 [project=%s]: %r", project_name, exc)
            await self._send_text(ctx, f"⚠️ 创建失败：{exc}")
            return
        if task.conversation_id:
            self._set_chat_entry("defaults", ctx.key, task.conversation_id)
        await self._send_text(
            ctx,
            f"🆕 已在 {project_name} 创建会话「{prompt[:32]}」，"
            f"任务 {task.id} 开始排队执行。之后的普通消息默认续聊这里。",
        )

    def _resolve_project(self, ctx: _MsgCtx, ref: str) -> Optional[str]:
        """项目引用 → 项目名。接受 /projects 列表编号或项目名本身。"""
        if self.scheduler is None:
            return None
        numbering = self._load_state().get("project_lists", {}).get(ctx.key, {})
        name = numbering.get(ref, ref)
        project = self.scheduler.find_project_by_name(name)
        return project.name if project else None

    async def _cmd_status(self, ctx: _MsgCtx, args: str) -> None:
        active = [
            t for t in (self.scheduler.list_tasks() if self.scheduler else [])
            if t.status in (TaskStatus.running, TaskStatus.pending, TaskStatus.scheduled)
        ]
        if ctx.project:
            active = [t for t in active if t.project_name == ctx.project]
        if not active:
            await self._send_text(ctx, "当前没有运行或排队中的任务。")
            return
        lines = ["进行中的任务："]
        for t in active:
            prompt = t.prompt.strip()[:40]
            conv_name = ""
            if t.conversation_id:
                conv = self.scheduler.get_conversation(t.conversation_id)
                conv_name = f"「{conv.name}」" if conv else ""
            lines.append(f"· {t.project_name or '?'}{conv_name} · {t.id} "
                         f"[{t.status.value}] {prompt}")
        await self._send_text(ctx, "\n".join(lines))

    async def _handle_voice_message(
        self, ctx: _MsgCtx, voice: dict, msg: dict
    ) -> None:
        if not self.asr_configured():
            await self._send_text(
                ctx, "语音转写未启用：请配置 TELEGRAM_ASR_API_KEY，或改用文本消息。")
            return
        try:
            text = await self._transcribe_voice(voice.get("file_id", ""))
        except Exception as exc:
            await self._send_text(ctx, f"⚠️ 语音转写失败：{exc}。请重试或改用文本消息。")
            return
        if not text:
            await self._send_text(ctx, "⚠️ 没有识别到语音内容，请重试或改用文本消息。")
            return
        # 先回转写文本让用户确认识别结果，再走常规续聊路由；
        # 两步式 /new 等待中的语音同样算"下一条消息"
        await self._send_text(ctx, f"🎧 已收到：{text}")
        pending_project = self._take_pending_new(ctx.key)
        if pending_project:
            await self._create_new_session(ctx, pending_project, text)
            return
        await self._submit_continuation(ctx, text, msg)

    async def _transcribe_voice(self, file_id: str) -> str:
        """Telegram 语音文件 → OpenAI 兼容 /audio/transcriptions → 文本。"""
        info = await self._api("getFile", {"file_id": file_id})
        file_path = info.get("file_path", "")
        if not file_path:
            raise TelegramError("getFile 未返回文件路径")
        async with httpx.AsyncClient(**self._client_kwargs(120.0)) as client:
            audio = await client.get(f"{_API_BASE}/file/bot{self.token}/{file_path}")
            if audio.status_code != 200:
                raise TelegramError(f"下载语音失败 HTTP {audio.status_code}")
            resp = await client.post(
                f"{self.asr_base_url}/audio/transcriptions",
                headers={"Authorization": f"Bearer {self.asr_api_key}"},
                data={"model": self.asr_model},
                files={"file": ("voice.oga", audio.content, "audio/ogg")},
            )
        if resp.status_code != 200:
            raise TelegramError(f"转写接口 HTTP {resp.status_code}: {resp.text[:200]}")
        return str(resp.json().get("text", "")).strip()

    def _route_conversation(self, msg: dict, ctx: _MsgCtx) -> tuple[str, str, bool]:
        """解析目标会话，优先级：reply 映射 → 本上下文 /use 默认 →
        项目话题内取该项目最近活跃会话 / 私聊与 General 取全局最近播报。

        返回 (conversation_id, project_name, reply_unmapped)：
        reply 指向未知消息时不回退——用户以为自己定向了某条任务链，
        静默误路由比拒绝更糟。
        """
        state = self._load_state()
        reply = msg.get("reply_to_message") or {}
        reply_id = str(reply.get("message_id", ""))
        if reply_id:
            mapped = state.get("messages", {}).get(reply_id)
            if mapped:
                return mapped["conversation_id"], mapped.get("project_name", ""), False
            return "", "", True
        default = state.get("defaults", {}).get(ctx.key, "")
        if default:
            return default, self._conversation_project_name(default), False
        if ctx.project:
            # 项目话题：只在本项目内路由，绝不落到其他项目的"最近播报"
            recent = self._recent_conversations(project=ctx.project)
            if recent:
                return recent[0].id, ctx.project, False
            return "", "", False
        last = state.get("last_conversation_id", "")
        if last:
            for entry in reversed(list(state.get("messages", {}).values())):
                if entry["conversation_id"] == last:
                    return last, entry.get("project_name", ""), False
            return last, "", False
        return "", "", False

    def _conversation_project_name(self, conversation_id: str) -> str:
        if self.scheduler is None:
            return ""
        try:
            conv = self.scheduler.get_conversation(conversation_id)
        except Exception:
            return ""
        return getattr(conv, "project_name", "") or ""

    async def _submit_continuation(self, ctx: _MsgCtx, prompt: str, msg: dict) -> None:
        conv_id, project_name, reply_unmapped = self._route_conversation(msg, ctx)
        logger.info("续聊路由 [ctx=%s] → conversation=%s%s",
                    ctx.key, conv_id or "无", "（reply 映射失效）" if reply_unmapped else "")
        if reply_unmapped:
            await self._send_text(
                ctx, "⚠️ 这条消息已无法关联会话（映射可能已过期），请回复最新一条任务播报。")
            return
        if not conv_id or self.scheduler is None:
            # 无路由目标的 "/xxx" 大概率是敲错的命令，回帮助比冷启动提示更有用
            if prompt.startswith("/"):
                hint = f"未识别的命令。\n\n{_HELP_TEXT}"
            elif ctx.project:
                hint = (f"{ctx.project} 还没有任何会话。"
                        f"发送 /new <指令> 即可在本项目开一条新会话链。")
            else:
                hint = _COLD_START_HINT
            await self._send_text(ctx, hint)
            return
        if self.scheduler.conversation_queue_full(conv_id):
            await self._send_text(
                ctx, "⚠️ 队列已满：该会话排队任务已达上限，请等待执行完成后再发。")
            return
        try:
            task = await self.scheduler.submit(prompt=prompt, conversation_id=conv_id)
            logger.info("续聊任务已提交 [conversation=%s task=%s]", conv_id, task.id)
        except Exception as exc:
            logger.warning("续聊提交失败 [conversation=%s]: %r", conv_id, exc)
            await self._send_text(ctx, f"⚠️ 提交失败：{exc}")
            return
        conv = None
        try:
            conv = self.scheduler.get_conversation(conv_id)
        except Exception:
            pass
        conv_name = getattr(conv, "name", "")
        target = project_name or "会话"
        if conv_name:
            target += f"「{conv_name}」"
        await self._send_text(ctx, f"📨 已提交到 {target}，任务 {task.id} 开始排队执行。")

    def start_polling(self) -> None:
        """在后台启动长轮询循环。未配置时循环空转等待，配置写入后自动生效。"""
        if self._poll_task is None or self._poll_task.done():
            self._poll_task = asyncio.create_task(self._poll_loop())

    async def _poll_loop(self) -> None:
        logger.info("Telegram 轮询循环启动 (workspace=%s)", self.workspace_dir)
        was_configured: Optional[bool] = None
        while True:
            try:
                configured = self.is_configured()
                if configured != was_configured:
                    # 只在配置状态翻转时打快照，避免每轮刷屏
                    if configured:
                        logger.info(
                            "Telegram 已配置：proxy=%s chats=%s mode=%s",
                            self.proxy or "直连", len(self.chat_ids), self.notify_mode,
                        )
                    else:
                        logger.info("Telegram 未配置（缺 token/chat_id），轮询待命")
                    was_configured = configured
                if not configured:
                    await asyncio.sleep(5)
                    continue
                await self.poll_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # %r：httpx 超时类异常 str() 为空，必须带类型才有诊断价值
                logger.warning("Telegram 轮询失败，10 秒后重试: %r", exc)
                await asyncio.sleep(10)
