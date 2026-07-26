"""
account_type_registry.py — 账号类型注册表

添加新账号类型时只需编辑此文件：
  1. 编写 _build_<type>() 和 _extract_<type>() 函数
  2. 在 ACCOUNT_TYPES 末尾注册一条 AccountTypeSpec

其余组件（compose、login、scheduler、config、前端）全部自动适配，无需额外改动。

AccountType 枚举同样从注册表自动派生（见末尾），也不需要手动同步。
"""
from __future__ import annotations

import json
import re
import shlex
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, List, Optional, Union


# ── 函数类型别名 ──────────────────────────────────────────────
#
# InnerCmdFn: 构建容器内 CLI 命令（inner_cmd）
#   参数：prompt, auto, task_id,
#         marker_quoted (shlex.quote(task_process_marker(task_id))),
#         task_env_quoted (shlex.quote(task_id)),
#         native_session_id, images
#   返回：shell 命令字符串（inner_cmd，不含日志重定向包装）
#
# ExtractFn: 从任务日志文本中提取 native_session_id
#   参数：log_text
#   返回：session_id 字符串，未找到时返回 ""
#
# EventsFn: 把一行已解码的任务输出 JSON 归一化成零或多个 OutputEvent
#   参数：line（单行 json.loads 后的 dict）
#   返回：OutputEvent 列表（纯函数，无 I/O，同 InnerCmdFn/ExtractFn 的约束一致）

InnerCmdFn = Callable[[str, bool, str, str, str, str, List[str], str], str]
ExtractFn  = Callable[[str], str]


# ── 归一化输出事件（issue #70）──────────────────────────────────
#
# log_parser.py 过去为每种账号类型各自维护一份 _parse_<type>_line，
# 六份实现各自编码"文本怎么拼、用量怎么算"的规则，且不从 ACCOUNT_TYPES
# 派生——这正是 pi 上线时静默漏挂输出解析、codex/opencode 事件形状各自
# 独立漂移两次都没有测试兜住的根因（见 issue #70 PRD）。
#
# 这里把"文本"和"用量/花费"归纳成一个只有 8 种成员、封闭的事件小词表；
# 每种事件都对应真实日志里已经出现过的、且只有这一种的合并规则——
# 规则本身只在 log_parser.py 的通用 reducer 里写一次，每种账号类型的
# `_events_<type>` 函数只负责"把一行判成哪些事件"，不重新决定合并方式。
#
# 文本：
#   TextChunk  —— 追加进累积缓冲区（大多数类型逐轮拼接的正常情况）。
#   FinalText  —— 直接覆盖最终文本，不经过缓冲区（目前只有 claude 的
#                 "result" 事件带有这种权威的最终答案）。
#
# 用量（token 计数），对应当前六种格式里实际出现过、且只有这三种的合并方式：
#   UsageDelta    —— 两个计数都累加（如 claude 的 "usage" 事件、
#                     codex 的 "usage"/"response.usage"）。
#   UsageProgress —— input 取历史最大值、output 累加（如 claude/
#                     opencode 的 assistant/usage 字段、pi 的 message_end）。
#   UsageTotal    —— 两个计数都被最新读数整体覆盖（如 codex 的
#                     "turn.completed"、grok 的 "end"）；某一侧字段缺失时
#                     传 None，reducer 只覆盖非 None 的那一侧——这保留了
#                     codex "turn.completed" 逐字段判断是否存在的原有行为，
#                     与 grok "end" 事件缺字段时默认为 0（而非 None，因此
#                     总是整体覆盖两侧）的行为是两回事，各自由分类函数自
#                     行决定传什么值，reducer 的合并规则本身保持通用。
#
# 花费（cost_usd），同样只有三种已出现的合并方式：
#   CostDelta     —— 累加（opencode 的 step_finish.cost）。
#   CostFirstSeen —— 只在当前还未设置时才生效（claude 的 "usage" 事件）。
#   CostTotal     —— 总是被最新读数整体覆盖（claude 的 "result" 事件、
#                     pi 的 usage.cost.total）。

@dataclass(frozen=True)
class TextChunk:
    text: str


@dataclass(frozen=True)
class FinalText:
    text: str


@dataclass(frozen=True)
class UsageDelta:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True)
class UsageProgress:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True)
class UsageTotal:
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None


@dataclass(frozen=True)
class CostDelta:
    cost_usd: float


@dataclass(frozen=True)
class CostFirstSeen:
    cost_usd: float


@dataclass(frozen=True)
class CostTotal:
    cost_usd: float


OutputEvent = Union[
    TextChunk, FinalText,
    UsageDelta, UsageProgress, UsageTotal,
    CostDelta, CostFirstSeen, CostTotal,
]
EventsFn = Callable[[dict], List[OutputEvent]]


# ── per-type inner command builders ──────────────────────────

_CF_SEND_SYSTEM_HINT = (
    "When the user asks you to send, share, or provide a file for download, "
    "output a download marker on its own line in this exact format: "
    "<!-- CF_SEND: relative/path/to/file.ext --> "
    "The path must be relative to /workspace (the project root). "
    "Examples: "
    "if the file is /workspace/report.html → output <!-- CF_SEND: report.html --> ; "
    "if the file is /tmp/output.pdf → first copy it to /workspace/ with: cp /tmp/output.pdf /workspace/ "
    "then output <!-- CF_SEND: output.pdf --> . "
    "Only output this marker after confirming the file exists at the given path. "
    "Do not mention scp, IDE downloads, or other manual methods — use this marker instead."
)


def _claude_mcp_bridge_arg() -> str:
    """issue #69 Slice 2：Intervention 桥接工具的 --mcp-config + --allowedTools 参数
    片段。这段字符串在所有任务、所有账号间字面完全相同——不接受任何参数——真正
    会变化的东西（relay 地址/端口、task id）全部由 Claude 自己的 ${VAR} 配置展开
    语法在容器里解析：CODERFLEET_RELAY_IP/CODERFLEET_MCP_BRIDGE_PORT 由
    compose.py 注入，CODERFLEET_TASK_ID 是 _build_claude 命令行本身已经在设置的
    同一个环境变量（见下面 exec 前缀），不必再单独插值一次任务 id 进 JSON 里。
    `${VAR:-default}` 兜底默认值是有意的：PROXY=off 的账号压根不会有
    CODERFLEET_RELAY_IP/CODERFLEET_MCP_BRIDGE_PORT 这两个环境变量（见
    compose.py），留空档位会让 Claude 在解析 --mcp-config 时直接报错、可能连
    带整个任务都起不来；有默认值时 Claude 正常启动，只有模型真的尝试调用这个
    工具时才会拿到一个连不通的地址、干净地报一次工具执行失败——比"整个任务开局
    就崩"这种后果轻得多，同时这也是为什么 Standards 复核里"PROXY=off 账号用不了
    Intervention"这条只是已知限制而不是需要另外报警的失败：调用会失败得很明显，
    不是静默"看起来成功了"。
    """
    mcp_config = {
        "mcpServers": {
            "coderfleet": {
                "type": "http",
                "url": "http://${CODERFLEET_RELAY_IP:-127.0.0.1}:${CODERFLEET_MCP_BRIDGE_PORT:-0}/mcp/",
                "headers": {"X-CoderFleet-Task-Id": "${CODERFLEET_TASK_ID}"},
            },
        },
    }
    config_json = shlex.quote(json.dumps(mcp_config))
    return f" --mcp-config {config_json} --allowedTools mcp__coderfleet__ask_user_question"


def _build_claude(prompt, auto, task_id, marker, task_env, session_id, images, model=""):
    p  = f"{prompt}\n\n[Attached images:\n" + "\n".join(images) + "]" if images else prompt
    ep = shlex.quote(p)
    perm   = "--dangerously-skip-permissions" if auto else "--permission-mode acceptEdits"
    resume = f" --resume {shlex.quote(session_id)}" if session_id else ""
    model_arg = f" --model {shlex.quote(model)}" if model else ""
    sys_hint = shlex.quote(_CF_SEND_SYSTEM_HINT)
    # auto 模式的承诺是"自己判断，别问"——绝不能给它挂一个能暂停等人的工具。
    mcp_bridge = "" if auto else _claude_mcp_bridge_arg()
    return (
        f"printf '%s\\n' {ep} | "
        f"CODERFLEET_TASK_ID={task_env} exec -a {marker} "
        f"claude -p {perm} --output-format stream-json --verbose"
        f"{model_arg} --append-system-prompt {sys_hint}{resume}{mcp_bridge}"
    )


def _build_codex(prompt, auto, task_id, marker, task_env, session_id, images, model=""):
    ep   = shlex.quote(prompt)
    imgs = "".join(f" -i {shlex.quote(i)}" for i in images)
    if session_id:
        danger = " --dangerously-bypass-approvals-and-sandbox" if auto else ""
        return (
            f"printf '%s\\n' {ep} | "
            f"CODERFLEET_TASK_ID={task_env} exec -a {marker} "
            f"codex exec resume {shlex.quote(session_id)} --json --skip-git-repo-check{danger}{imgs}"
        )
    sandbox = "danger-full-access" if auto else "workspace-write"
    return (
        f"printf '%s\\n' {ep} | "
        f"CODERFLEET_TASK_ID={task_env} exec -a {marker} "
        f"codex exec --json --skip-git-repo-check --sandbox {sandbox}{imgs}"
    )


def _build_opencode(prompt, auto, task_id, marker, task_env, session_id, images, model=""):
    ep    = shlex.quote(prompt)
    perm  = " --dangerously-skip-permissions" if auto else ""
    sess  = f" --session {shlex.quote(session_id)}" if session_id else ""
    files = "".join(f" --file {shlex.quote(i)}" for i in images)
    return (
        f"CODERFLEET_TASK_ID={task_env} exec -a {marker} "
        f"opencode run --format json{perm}{sess}{files} {ep}"
    )


def _build_hermes(prompt, auto, task_id, marker, task_env, session_id, images, model=""):
    ep     = shlex.quote(prompt)
    yolo   = " --yolo" if auto else ""
    resume = f" --resume {shlex.quote(session_id)}" if session_id else ""
    return (
        f"CODERFLEET_TASK_ID={task_env} exec -a {marker} "
        f"/opt/hermes-venv/bin/hermes{resume} chat -q {ep}{yolo}"
    )


def _build_grok(prompt, auto, task_id, marker, task_env, session_id, images, model=""):
    p    = f"{prompt}\n\n[Attached images:\n" + "\n".join(images) + "]" if images else prompt
    ep   = shlex.quote(p)
    qsid = shlex.quote(session_id) if session_id else ""
    approve = " --always-approve" if auto else ""
    resume = f" --resume {qsid}" if session_id else ""
    marker_line = f"printf 'grok_session_id=%s\\n' {qsid} && " if session_id else ""
    # 新会话不指定 --session-id：当前 Grok CLI 要求它必须是 UUID，
    # CoderFleet task_id 不是 UUID。会话 ID 从 streaming-json 输出提取。
    return (
        f"{marker_line}"
        f"CODERFLEET_TASK_ID={task_env} exec -a {marker} "
        f"grok -p {ep}{resume}{approve} --no-auto-update --output-format streaming-json"
    )


def _build_kimi(prompt, auto, task_id, marker, task_env, session_id, images, model=""):
    p = f"{prompt}\n\n[Attached images:\n" + "\n".join(images) + "]" if images else prompt
    ep = shlex.quote(p)
    sess = f" --session {shlex.quote(session_id)}" if session_id else ""
    return (
        f"CODERFLEET_TASK_ID={task_env} exec -a {marker} "
        f"kimi{sess} -p {ep} --output-format stream-json"
    )


def _build_pi(prompt, auto, task_id, marker, task_env, session_id, images, model=""):
    ep = shlex.quote(prompt)
    # pi's real attachment mechanism (docs/usage.md "File Arguments"): `@path`
    # positional args before the message, e.g. `pi @screenshot.png "..."`. For
    # images this attaches actual multimodal bytes, not just a path reference
    # — unlike claude/grok/kimi's builders, don't fold these into prompt text.
    file_args = "".join(f" {shlex.quote('@' + i)}" for i in images)
    session   = f" --session {shlex.quote(session_id)}" if session_id else ""
    model_arg = f" --model {shlex.quote(model)}" if model else ""
    # pi has no tool-permission confirmation system (its own docs: "No permission
    # popups. Run in a container.") — --approve/--no-approve only gates trusting
    # project-local .pi/ config, not tool execution.
    approve = " --approve" if auto else " --no-approve"
    return (
        f"CODERFLEET_TASK_ID={task_env} exec -a {marker} "
        f"pi --mode json{session}{model_arg}{approve}{file_args} {ep}"
    )


# ── per-type output-event classifiers (issue #70) ──────────────
# 一行 json.loads 后的输出 → 零或多个 OutputEvent。纯函数，无 I/O，与
# _build_<type>/_extract_<type> 的约束一致，同样直接单测，不需要 Docker。

def _events_kimi(d: dict) -> List[OutputEvent]:
    if d.get("role") != "assistant":
        return []
    content = d.get("content", "")
    if isinstance(content, str):
        return [TextChunk(content)]
    events: List[OutputEvent] = []
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                events.append(TextChunk(str(block.get("text", ""))))
    return events


def _events_grok(d: dict) -> List[OutputEvent]:
    t = d.get("type", "")
    if t == "text_delta":
        return [TextChunk(d.get("text", ""))]
    if t == "text":
        return [TextChunk(d.get("data", d.get("text", "")))]
    if t == "end":
        usage = d.get("usage", {})
        if isinstance(usage, dict):
            return [UsageTotal(
                int(usage.get("input_tokens", 0)),
                int(usage.get("output_tokens", 0)),
            )]
    return []


def _events_pi(d: dict) -> List[OutputEvent]:
    # `--mode json` emits one `message_end` per completed message; the final
    # answer is whichever assistant message_end(s) land last, since pi has
    # no single dedicated "result" event like claude's `type=="result"`.
    # `agent_end` re-lists the same messages, so it's intentionally not read
    # here to avoid double-counting text/usage across duplicate coverage of
    # the same turn.
    if d.get("type") != "message_end":
        return []
    msg = d.get("message", {})
    if msg.get("role") != "assistant":
        return []
    events: List[OutputEvent] = [
        TextChunk(block.get("text", ""))
        for block in msg.get("content", [])
        if isinstance(block, dict) and block.get("type") == "text"
    ]
    usage = msg.get("usage", {})
    if isinstance(usage, dict) and usage:
        events.append(UsageProgress(int(usage.get("input", 0)), int(usage.get("output", 0))))
        cost = usage.get("cost", {})
        if isinstance(cost, dict) and cost.get("total"):
            events.append(CostTotal(float(cost["total"])))
    return events


def _events_claude(d: dict) -> List[OutputEvent]:
    t = d.get("type", "")
    events: List[OutputEvent] = []
    if t == "result":
        # {"type":"result","result":"...","cost_usd":0.02,"session_id":"..."}
        # This event's cost always overwrites (CostTotal) -- distinct from
        # the "usage" event below, whose cost only sets if not already set
        # (CostFirstSeen). Claude's cost accounting is genuinely these two
        # different rules depending on which event carries it, not one rule.
        r = d.get("result", "")
        if r:
            events.append(FinalText(str(r)))
        for key in ("cost_usd", "total_cost_usd"):
            c = d.get(key)
            if c:
                events.append(CostTotal(float(c)))
                break
    elif t == "assistant":
        msg = d.get("message", {})
        for block in msg.get("content", []):
            if isinstance(block, dict) and block.get("type") == "text":
                events.append(TextChunk(block["text"]))
        usage = msg.get("usage", {})
        if usage:
            events.append(UsageProgress(int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0))))
    elif t == "usage":
        events.append(UsageDelta(int(d.get("input_tokens", 0)), int(d.get("output_tokens", 0))))
        c = d.get("cost_usd") or d.get("total_cost_usd", 0)
        if c:
            events.append(CostFirstSeen(float(c)))
    return events


def _events_codex(d: dict) -> List[OutputEvent]:
    t = d.get("type", "")
    if t == "message" and d.get("role") == "assistant":
        return [
            TextChunk(block["text"])
            for block in d.get("content", [])
            if isinstance(block, dict) and block.get("type") == "text"
        ]
    if t == "item.completed":
        item = d.get("item")
        if isinstance(item, dict) and item.get("type") == "agent_message":
            text = item.get("text")
            if text:
                return [TextChunk(text)]
        return []
    if t in ("usage", "response.usage"):
        return [UsageDelta(
            int(d.get("prompt_tokens", 0) or d.get("input_tokens", 0)),
            int(d.get("completion_tokens", 0) or d.get("output_tokens", 0)),
        )]
    if t == "turn.completed":
        # turn.completed's usage is a cumulative total for the whole thread
        # (confirmed against Codex's own --json schema), not a per-turn
        # delta -- UsageTotal overwrites rather than accumulates, so a
        # multi-turn thread ends up with the final total instead of
        # double-counting overlapping snapshots. Each field is passed
        # through as None when absent so the reducer leaves that side
        # untouched rather than zeroing it out.
        usage = d.get("usage")
        if isinstance(usage, dict):
            input_tokens = usage.get("input_tokens")
            output_tokens = usage.get("output_tokens")
            return [UsageTotal(
                int(input_tokens) if input_tokens is not None else None,
                int(output_tokens) if output_tokens is not None else None,
            )]
        return []
    return []


def _events_opencode(d: dict) -> List[OutputEvent]:
    # Current OpenCode CLI output nests everything under a `part` object
    # (matching renderer.js's _opencodeText/_opencodeStepFinish, which read
    # d.part.text / d.part.tokens) -- the flat top-level `content`/`usage`
    # legacy-shape handling below is a defensive fallback for older-format
    # logs still on disk, not the primary path.
    #
    # Two independent checks below (text-vs-legacy-content, then
    # step_finish, then generic top-level usage) -- not an if/elif chain --
    # because real OpenCode logs can carry more than one of these on the
    # same line and the original parser accumulated across all of them.
    t = d.get("type", "")
    part = d.get("part")
    part = part if isinstance(part, dict) else {}
    events: List[OutputEvent] = []

    if t == "text" and part.get("type") == "text":
        text = part.get("text")
        if text:
            events.append(TextChunk(text))
    elif d.get("role") == "assistant" or t in ("message", "assistant"):
        content = d.get("content", [])
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    events.append(TextChunk(block["text"]))
        elif isinstance(content, str):
            events.append(TextChunk(content))

    if t == "step_finish" and part.get("type") == "step-finish":
        tokens = part.get("tokens")
        tokens = tokens if isinstance(tokens, dict) else {}
        cache = tokens.get("cache")
        cache = cache if isinstance(cache, dict) else {}
        input_tokens = (
            int(tokens.get("input", 0) or 0)
            + int(cache.get("read", 0) or 0)
            + int(cache.get("write", 0) or 0)
        )
        output_tokens = int(tokens.get("output", 0) or 0) + int(tokens.get("reasoning", 0) or 0)
        events.append(UsageDelta(input_tokens, output_tokens))
        cost = part.get("cost")
        if cost:
            events.append(CostDelta(float(cost)))

    usage = d.get("usage", {})
    if isinstance(usage, dict) and usage:
        events.append(UsageProgress(int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0))))

    return events


# ── per-type session ID extractors ────────────────────────────

def _extract_claude(text):
    for line in text.splitlines():
        s = line.strip()
        if not s.startswith("{"):
            continue
        try:
            d = json.loads(s)
        except json.JSONDecodeError:
            continue
        if d.get("session_id"):
            return str(d["session_id"])
    return ""


def _extract_codex(text):
    for line in text.splitlines():
        s = line.strip()
        if not s.startswith("{"):
            continue
        try:
            d = json.loads(s)
        except json.JSONDecodeError:
            continue
        if d.get("type") == "thread.started" and d.get("thread_id"):
            return str(d["thread_id"])
    return ""


def _extract_opencode(text):
    for line in text.splitlines():
        s = line.strip()
        if not s.startswith("{"):
            continue
        try:
            d = json.loads(s)
        except json.JSONDecodeError:
            continue
        for key in ("sessionID", "session_id", "sessionId"):
            if d.get(key):
                return str(d[key])
        sess = d.get("session")
        if isinstance(sess, dict):
            for key in ("id", "sessionID", "session_id", "sessionId"):
                if sess.get(key):
                    return str(sess[key])
        if "session" in str(d.get("type", "")).lower():
            for key in ("id", "sessionID", "session_id", "sessionId"):
                if d.get(key):
                    return str(d[key])
    return ""


def _extract_hermes(text):
    m = re.search(r"^Session:\s+(\S+)", text, re.MULTILINE)
    return m.group(1) if m else ""


def _extract_grok(text):
    # 优先从我们自己写入的标记行提取（最可靠）
    m = re.search(r"^grok_session_id=(\S+)", text, re.MULTILINE)
    if m:
        return m.group(1)
    # 降级：从 streaming-json 的 end 事件提取
    # end 事件格式：{"type":"end","stopReason":"...","sessionId":"...","requestId":"..."}
    for line in text.splitlines():
        s = line.strip()
        if not s.startswith("{"):
            continue
        try:
            d = json.loads(s)
        except json.JSONDecodeError:
            continue
        for key in ("sessionId", "session_id", "sessionID"):
            if d.get(key):
                return str(d[key])
    return ""


def _extract_kimi(text):
    for line in text.splitlines():
        s = line.strip()
        if not s.startswith("{"):
            continue
        try:
            d = json.loads(s)
        except json.JSONDecodeError:
            continue
        if d.get("role") == "meta" and d.get("type") == "session.resume_hint":
            if d.get("session_id"):
                return str(d["session_id"])
    m = re.search(r"To resume this session:\s+kimi\s+-r\s+(\S+)", text)
    return m.group(1) if m else ""


def _extract_pi(text):
    # `--mode json` always writes the session header as its first JSON line:
    # {"type":"session","version":3,"id":"...","timestamp":"...","cwd":"..."}
    for line in text.splitlines():
        s = line.strip()
        if not s.startswith("{"):
            continue
        try:
            d = json.loads(s)
        except json.JSONDecodeError:
            continue
        if d.get("type") == "session" and d.get("id"):
            return str(d["id"])
    return ""


# ── AccountTypeSpec ───────────────────────────────────────────

@dataclass
class AccountTypeSpec:
    id:                 str
    label:              str             # UI 显示名称
    auth_dir:           str             # 容器内认证数据挂载路径
    login_cli:          str             # 登录命令的二进制名称
    login_args:         List[str]       # 登录子命令参数列表
    supports_env_auth:  bool            # 是否支持 AUTH=env 认证方式
    env_vars:           dict            # 注入容器的静态环境变量（compose + 临时容器）
    env_hint:           str             # AUTH=env 时在 UI/CLI 显示的配置提示（可含 \n）
    badge_bg:           str             # 账号卡片徽章背景色（任意 CSS 值）
    badge_color:        str             # 账号卡片徽章文字色（任意 CSS 值）
    build_inner_cmd:    InnerCmdFn      # 构建 inner_cmd 的函数
    extract_session_id: ExtractFn       # 从日志文本提取 native_session_id 的函数
    usage_status_cmd:   str = ""        # 任务完成后查询用量的 shell 命令（空=跳过）
    parse_output_events: Optional[EventsFn] = None
    # 把一行任务输出 JSON 归一化成 OutputEvent 列表的纯函数；None 表示该类型
    # 没有结构化的逐行输出格式（目前只有 hermes——纯文本，走 log_parser.py
    # 通用的 _parse_text_fallback，不参与这里的事件归一化）。


# ── 注册表 ────────────────────────────────────────────────────
# 新增账号类型：在此添加一条 AccountTypeSpec，其余全自动。

ACCOUNT_TYPES: dict[str, AccountTypeSpec] = {
    "claude": AccountTypeSpec(
        id="claude", label="Claude Code",
        auth_dir="/home/byclaw/.claude",
        login_cli="claude", login_args=["login"],
        supports_env_auth=True,
        env_vars={},
        env_hint=(
            "请在 env 文件中配置 ANTHROPIC_API_KEY 等环境变量\n"
            "示例：ANTHROPIC_API_KEY=sk-ant-..."
        ),
        badge_bg="var(--amber-bg)", badge_color="var(--amber)",
        build_inner_cmd=_build_claude,
        extract_session_id=_extract_claude,
        parse_output_events=_events_claude,
    ),
    "codex": AccountTypeSpec(
        id="codex", label="Codex CLI",
        auth_dir="/home/byclaw/.codex",
        login_cli="codex", login_args=["login", "--device-auth"],
        supports_env_auth=False,
        env_vars={},
        env_hint="",
        badge_bg="var(--accent-bg)", badge_color="var(--accent)",
        build_inner_cmd=_build_codex,
        extract_session_id=_extract_codex,
        usage_status_cmd="coderfleet-usage-status codex 2>&1",
        parse_output_events=_events_codex,
    ),
    "opencode": AccountTypeSpec(
        id="opencode", label="OpenCode",
        auth_dir="/home/byclaw/.opencode",
        login_cli="opencode", login_args=["auth", "login"],
        supports_env_auth=True,
        env_vars={
            "XDG_DATA_HOME":   "/home/byclaw/.opencode/data",
            "XDG_CONFIG_HOME": "/home/byclaw/.opencode/config",
            "XDG_STATE_HOME":  "/home/byclaw/.opencode/state",
            "XDG_CACHE_HOME":  "/home/byclaw/.opencode/cache",
        },
        env_hint="请在 env 文件中配置 API Key 等环境变量",
        badge_bg="var(--green-bg)", badge_color="var(--green)",
        build_inner_cmd=_build_opencode,
        extract_session_id=_extract_opencode,
        parse_output_events=_events_opencode,
    ),
    "hermes": AccountTypeSpec(
        id="hermes", label="Hermes Agent",
        auth_dir="/home/byclaw/.hermes",
        login_cli="hermes", login_args=["setup"],
        supports_env_auth=True,
        env_vars={"HERMES_HOME": "/home/byclaw/.hermes"},
        env_hint=(
            "请在 env 文件中配置 LLM provider API key\n"
            "示例：ANTHROPIC_API_KEY=sk-ant-...  或  OPENAI_API_KEY=sk-...\n"
            "然后运行 hermes config set model.provider anthropic 等完成初始化"
        ),
        badge_bg="#2d1f4e", badge_color="#b48ef5",
        build_inner_cmd=_build_hermes,
        extract_session_id=_extract_hermes,
    ),
    "grok": AccountTypeSpec(
        id="grok", label="Grok Build",
        auth_dir="/home/byclaw/.grok",
        login_cli="grok", login_args=["--no-auto-update"],
        supports_env_auth=True,
        env_vars={
            "GROK_HOME":           "/home/byclaw/.grok",
            "GROK_NO_AUTO_UPDATE": "1",
        },
        env_hint=(
            "请在 env 文件中配置 XAI_API_KEY\n"
            "示例：XAI_API_KEY=xai-...\n"
            "获取 API key：https://console.x.ai/"
        ),
        badge_bg="#0f1923", badge_color="#38bdf8",
        build_inner_cmd=_build_grok,
        extract_session_id=_extract_grok,
        parse_output_events=_events_grok,
    ),
    "kimi": AccountTypeSpec(
        id="kimi", label="Kimi Code",
        auth_dir="/home/byclaw/.kimi-code",
        login_cli="kimi", login_args=["login"],
        supports_env_auth=True,
        env_vars={
            "KIMI_CODE_HOME":           "/home/byclaw/.kimi-code",
            "KIMI_CODE_NO_AUTO_UPDATE": "1",
            "KIMI_DISABLE_TELEMETRY":   "1",
        },
        env_hint=(
            "请在 env 文件中配置 KIMI_MODEL_* 环境变量\n"
            "示例：KIMI_MODEL_NAME=kimi-for-coding\n"
            "示例：KIMI_MODEL_API_KEY=sk-...\n"
            "可选：KIMI_MODEL_BASE_URL=https://api.moonshot.ai/v1"
        ),
        badge_bg="#101820", badge_color="#7dd3fc",
        build_inner_cmd=_build_kimi,
        extract_session_id=_extract_kimi,
        parse_output_events=_events_kimi,
    ),
    "pi": AccountTypeSpec(
        id="pi", label="Pi Agent",
        auth_dir="/home/byclaw/.pi/agent",
        login_cli="pi", login_args=[],
        supports_env_auth=True,
        env_vars={
            "PI_SKIP_VERSION_CHECK": "1",
            "PI_TELEMETRY":          "0",
        },
        env_hint=(
            "请在 env 文件中配置 provider API Key，pi 原生支持 20+ providers\n"
            "示例：ANTHROPIC_API_KEY=sk-ant-...  或  OPENAI_API_KEY=sk-...\n"
            "登录（含订阅制 OAuth）没有无头命令，请用 coderfleet login 进入交互式\n"
            "pi 会话后手动执行 /login"
        ),
        badge_bg="#1a2e1a", badge_color="#7ee787",
        build_inner_cmd=_build_pi,
        extract_session_id=_extract_pi,
        parse_output_events=_events_pi,
    ),
}


# ── 公开 API ──────────────────────────────────────────────────

def get_spec(type_id: str) -> AccountTypeSpec:
    """返回账号类型的 spec，未知类型抛出 KeyError。"""
    return ACCOUNT_TYPES[type_id]


def valid_type_ids() -> list[str]:
    """返回所有已注册账号类型的 ID 列表。"""
    return list(ACCOUNT_TYPES.keys())


def env_auth_type_ids() -> list[str]:
    """返回支持 AUTH=env 认证方式的账号类型 ID 列表。"""
    return [t for t, s in ACCOUNT_TYPES.items() if s.supports_env_auth]


def duplicate_account_types(account_types: list[str]) -> list[str]:
    """返回 account_types 中重复出现的 TYPE 值（去重，保持首次重复出现的顺序）。

    用于校验一个项目绑定的账号集合（主账号 + 从账号）：每种 TYPE 在容器内
    只有一个固定挂载路径，同一项目不能绑定两个相同 TYPE 的账号。
    """
    seen: set[str] = set()
    dupes: list[str] = []
    for t in account_types:
        if t in seen and t not in dupes:
            dupes.append(t)
        seen.add(t)
    return dupes


# ── AccountType 枚举（从注册表自动派生，勿手动编辑）───────────
# 与 class AccountType(str, Enum) 等价，支持 AccountType.claude 等属性访问。

AccountType = Enum("AccountType", {k: k for k in ACCOUNT_TYPES}, type=str)
