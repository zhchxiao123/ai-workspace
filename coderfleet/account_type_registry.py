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
#         native_session_id, images, model,
#         local_sandboxed（默认 False；只有 runtime=local 且账号已确认开启自带 OS 级沙箱的
#         账号才会传 True —— 目前只有 _build_codex 真的据此换旗标，其余类型接受但忽略，
#         保持 InnerCmdFn 签名统一）
#   返回：shell 命令字符串（inner_cmd，不含日志重定向包装）
#
# ExtractFn: 从任务日志文本中提取 native_session_id
#   参数：log_text
#   返回：session_id 字符串，未找到时返回 ""
#
# EventsFn: 把一行已解码的任务输出 JSON 归一化成零或多个 OutputEvent
#   参数：line（单行 json.loads 后的 dict）
#   返回：OutputEvent 列表（纯函数，无 I/O，同 InnerCmdFn/ExtractFn 的约束一致）

InnerCmdFn = Callable[[str, bool, str, str, str, str, List[str], str, bool], str]
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


# ── 工具调用（issue #71）────────────────────────────────────────
#
# 上面 8 种成员只覆盖"文本 + 用量/花费"，也就是 TaskOutputData 已有的字段。
# 工具调用是各 CLI 输出里差异最大的一维，过去只有 renderer.js 知道，后端
# 完全无法回答"这个任务实际做了什么"——这两个成员把它补上。
#
# 拆成 intent/outcome 两个事件（而不是一个"已完成的工具调用"）是必须的：
# 四种格式全都把"请求"和"结果"分在两行，靠一个 id 关联，而其中三种给这个
# id 用了不同的字段名。合成一个事件就要求分类函数跨行保存状态，直接破坏
# "一行进、零或多个事件出"的纯函数约束（那是这些函数不需要 Docker 就能
# 单测的前提）。
#
#   ToolIntent  —— 请求发起：call_id + 工具名 + 参数。
#   ToolOutcome —— 请求完成：call_id + 结果文本 + 是否报错。
#
# 归一化的是**字段名**，不是值：call_id 原样透传（claude 的 tool_use_id、
# codex 的 tool_call_id、pi 的 toolCallId 都落到 call_id 上），因为它只在
# 单个日志内部做相等比较，不需要规范化。arguments 一律由各分类函数负责
# 归一成 dict——有些格式（codex/kimi）会把它编码成 JSON 字符串，解码的
# 责任留在分类函数里，消费方只看到一种形状。
#
# 这两种事件不折叠进 TaskOutputData（它只有 text/token/cost 四个字段，
# 工具调用不属于其中任何一个）；log_parser.py 的 reducer 对它们显式空转，
# 本 issue 的真实读路径是覆盖率探测器 detect_unrecognized_shapes()。

@dataclass(frozen=True)
class ToolIntent:
    call_id: str
    name: str
    arguments: dict


@dataclass(frozen=True)
class ToolOutcome:
    call_id: str
    result_text: str = ""
    is_error: bool = False


OutputEvent = Union[
    TextChunk, FinalText,
    UsageDelta, UsageProgress, UsageTotal,
    CostDelta, CostFirstSeen, CostTotal,
    ToolIntent, ToolOutcome,
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


def _claude_mcp_bridge_arg(*, allow_ask_user: bool) -> str:
    """CoderFleet MCP 的配置与工具权限参数。

    MCP server 配置在普通/全自动任务间保持字面相同；变化的只有工具权限：普通
    模式预批准 ask_user_question + schedule_continuation，全自动模式只预批准
    schedule_continuation，并显式 disallow ask_user_question，防止
    --dangerously-skip-permissions 把人工等待工具也放行。

    真正会变化的东西（relay 地址/端口、task id）全部由 Claude 自己的 ${VAR} 配置展开
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
    ask_tool = "mcp__coderfleet__ask_user_question"
    continuation_tool = "mcp__coderfleet__schedule_continuation"
    allowed = f"{ask_tool},{continuation_tool}" if allow_ask_user else continuation_tool
    denied = "" if allow_ask_user else f" --disallowedTools {ask_tool}"
    return f" --mcp-config {config_json} --allowedTools {allowed}{denied}"


def _build_claude(prompt, auto, task_id, marker, task_env, session_id, images, model="", local_sandboxed=False):
    # local_sandboxed 对 Claude 是接受但不使用的参数（跟其余非 claude/codex 类型一样只为了
    # InnerCmdFn 签名统一）：--dangerously-skip-permissions 只跳过审批 UI，真正的隔离靠账号
    # 自己在 sandbox_confirmed 门禁下已经手工开启的 Claude Code 自带 OS 级沙箱
    # （sandbox.enabled + failIfUnavailable），两者不冲突，不需要在这里换旗标。
    p  = f"{prompt}\n\n[Attached images:\n" + "\n".join(images) + "]" if images else prompt
    ep = shlex.quote(p)
    perm   = "--dangerously-skip-permissions" if auto else "--permission-mode acceptEdits"
    resume = f" --resume {shlex.quote(session_id)}" if session_id else ""
    model_arg = f" --model {shlex.quote(model)}" if model else ""
    sys_hint = shlex.quote(_CF_SEND_SYSTEM_HINT)
    # auto 模式只禁止会暂停等人的 ask_user_question；Continuation 是自动编排
    # 能力，普通/全自动任务都必须可用。
    mcp_bridge = _claude_mcp_bridge_arg(allow_ask_user=not auto)
    return (
        f"printf '%s\\n' {ep} | "
        f"CODERFLEET_TASK_ID={task_env} exec -a {marker} "
        f"claude -p {perm} --output-format stream-json --verbose"
        f"{model_arg} --append-system-prompt {sys_hint}{resume}{mcp_bridge}"
    )


def _build_codex(prompt, auto, task_id, marker, task_env, session_id, images, model="", local_sandboxed=False):
    ep   = shlex.quote(prompt)
    imgs = "".join(f" -i {shlex.quote(i)}" for i in images)
    if session_id:
        if local_sandboxed:
            # local runtime 没有容器兜底：换成 Codex 自己的 --sandbox，而不是假设外部已经
            # 隔离好的 --dangerously-bypass-approvals-and-sandbox —— 这个旗标自己的 --help
            # 原话是 "Intended solely for running in environments that are externally
            # sandboxed"，本地场景不满足这个前提。
            sandbox_arg = " --sandbox workspace-write"
        else:
            sandbox_arg = " --dangerously-bypass-approvals-and-sandbox" if auto else ""
        return (
            f"printf '%s\\n' {ep} | "
            f"CODERFLEET_TASK_ID={task_env} exec -a {marker} "
            f"codex exec resume {shlex.quote(session_id)} --json --skip-git-repo-check{sandbox_arg}{imgs}"
        )
    sandbox = "workspace-write" if (local_sandboxed or not auto) else "danger-full-access"
    return (
        f"printf '%s\\n' {ep} | "
        f"CODERFLEET_TASK_ID={task_env} exec -a {marker} "
        f"codex exec --json --skip-git-repo-check --sandbox {sandbox}{imgs}"
    )


def _build_opencode(prompt, auto, task_id, marker, task_env, session_id, images, model="", local_sandboxed=False):
    ep    = shlex.quote(prompt)
    perm  = " --dangerously-skip-permissions" if auto else ""
    sess  = f" --session {shlex.quote(session_id)}" if session_id else ""
    files = "".join(f" --file {shlex.quote(i)}" for i in images)
    return (
        f"CODERFLEET_TASK_ID={task_env} exec -a {marker} "
        f"opencode run --format json{perm}{sess}{files} {ep}"
    )


def _build_hermes(prompt, auto, task_id, marker, task_env, session_id, images, model="", local_sandboxed=False):
    ep     = shlex.quote(prompt)
    yolo   = " --yolo" if auto else ""
    resume = f" --resume {shlex.quote(session_id)}" if session_id else ""
    return (
        f"CODERFLEET_TASK_ID={task_env} exec -a {marker} "
        f"/opt/hermes-venv/bin/hermes{resume} chat -q {ep}{yolo}"
    )


def _build_grok(prompt, auto, task_id, marker, task_env, session_id, images, model="", local_sandboxed=False):
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


def _build_kimi(prompt, auto, task_id, marker, task_env, session_id, images, model="", local_sandboxed=False):
    p = f"{prompt}\n\n[Attached images:\n" + "\n".join(images) + "]" if images else prompt
    ep = shlex.quote(p)
    sess = f" --session {shlex.quote(session_id)}" if session_id else ""
    return (
        f"CODERFLEET_TASK_ID={task_env} exec -a {marker} "
        f"kimi{sess} -p {ep} --output-format stream-json"
    )


def _build_pi(prompt, auto, task_id, marker, task_env, session_id, images, model="", local_sandboxed=False):
    # local_sandboxed 接受但不使用：pi 自己的文档明确写着 "No permission popups. Run in a
    # container." ——它没有类似 Claude sandbox.enabled / Codex --sandbox 的自带 OS 级隔离可以
    # 换用，local runtime 场景下即使 sandbox_confirmed=True 也无法真正兑现"已隔离"这个前提。
    # 这一期 sandbox 门禁只覆盖 claude/codex（PRD 场景 5 范围），pi 的 local 支持是后续项。
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
    # kimi 是唯一一种**不靠 type 而靠 role 判别行形状**的格式（见下面的
    # _shape_kimi）。它的工具调用是 OpenAI 风格：请求不是一整行，而是挂在
    # assistant 行上的一个 tool_calls 数组——一行可以带好几次调用。
    #
    # 注意：kimi 至今不报 token/cost，这是 #70 起就照抄的已知缺口，issue #79
    # 明确不在这里修。
    role = d.get("role")
    if role == "tool":
        # 结果行用的是另一个字段名 tool_call_id，归一到 call_id。
        #
        # **决定（不是默认值）**：kimi 的工具结果行压根没有错误标志字段，跟
        # 其他所有格式都不一样。这里一律报 is_error=False，而不是去猜内容里
        # 有没有 "Error"——猜出来的失败率一定不准，而一个稳定的 False 至少
        # 语义明确："这个格式不告诉我们成功还是失败"。真要区分，得等 kimi
        # 自己在输出里给出信号。
        return [ToolOutcome(str(d.get("tool_call_id", "")), str(d.get("content") or ""), False)]

    if role != "assistant":
        return []

    events: List[OutputEvent] = []
    content = d.get("content", "")
    if isinstance(content, str):
        if content:
            events.append(TextChunk(content))
    elif isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                events.append(TextChunk(str(block.get("text", ""))))

    tool_calls = d.get("tool_calls")
    if isinstance(tool_calls, list):
        for tc in tool_calls:
            if not isinstance(tc, dict):
                continue
            fn = tc.get("function")
            fn = fn if isinstance(fn, dict) else {}
            events.append(ToolIntent(
                str(tc.get("id", "")),
                str(fn.get("name", "") or ""),
                # arguments 可能是对象，也可能是 JSON 字符串——解不出来的字符串
                # 由 _as_arguments_dict 原样保留，不丢也不抛。
                _as_arguments_dict(fn.get("arguments")),
            ))
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
    t = d.get("type", "")
    if t == "tool_execution_start":
        # pi is the only format that gives tool calls their own dedicated
        # start/end event types — one call per line, no nesting, no shared
        # event type between request and result.
        return [ToolIntent(
            str(d.get("toolCallId", "")),
            str(d.get("toolName", "")),
            d.get("args") if isinstance(d.get("args"), dict) else {},
        )]
    if t == "tool_execution_end":
        # Result arrives either as {"content": [{"text": ...}, ...]} or as a
        # bare string; anything else yields empty text rather than a repr.
        result = d.get("result")
        if isinstance(result, dict) and isinstance(result.get("content"), list):
            text = "\n".join(
                str(c.get("text", ""))
                for c in result["content"]
                if isinstance(c, dict)
            )
        elif isinstance(result, str):
            text = result
        else:
            text = ""
        return [ToolOutcome(str(d.get("toolCallId", "")), text, bool(d.get("isError")))]
    if t != "message_end":
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


def _as_arguments_dict(value) -> dict:
    """
    工具参数 → dict，永远是 dict（OutputEvent 的约定，见 ToolIntent）。

    有些格式（codex 老版本、kimi）把参数编码成 JSON 字符串。解码责任留在
    分类函数这一侧，消费方只看到一种形状。解不出来的字符串**不丢弃**——
    原样塞进 {"arguments": ...}，因为"参数长什么样"本身就是排查 CLI 格式
    漂移时最想看到的东西；丢了就等于把线索删了。
    """
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {"arguments": value}
        return parsed if isinstance(parsed, dict) else {"arguments": value}
    return {}


def _claude_tool_result_text(content) -> str:
    """
    tool_result.content 的两种编码 → 一段文本。

    数组那种编码踩过一次真实的坑：Read 命中图片文件时，数组里会混进一个
    **没有 .text 的 image block**，直接 join 出来就是空字符串，整段工具输出
    悄悄消失（renderer.js 已经为此修过一次）。所以这里只取 text block，
    其余（image 等）跳过而不是当成空串拼进去。
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(c.get("text", ""))
            for c in content
            if isinstance(c, dict) and c.get("type") == "text"
        )
    return ""


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
        # claude 的工具事件是**嵌在行里的 block**，不是行本身的形状（issue #75）：
        # 一条 assistant 行的 message.content[] 里可以同时躺着 text / thinking /
        # tool_use，所以这里一次遍历按原顺序产出多个事件，而不是每种 block 各扫
        # 一遍——顺序就是模型实际输出的顺序，消费方能直接拿来重建时间线。
        msg = d.get("message", {})
        for block in msg.get("content", []):
            if not isinstance(block, dict):
                continue
            bt = block.get("type")
            if bt == "text":
                events.append(TextChunk(block["text"]))
            elif bt == "tool_use":
                events.append(ToolIntent(
                    str(block.get("id", "")),
                    str(block.get("name", "")),
                    block.get("input") if isinstance(block.get("input"), dict) else {},
                ))
        usage = msg.get("usage", {})
        if usage:
            events.append(UsageProgress(int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0))))
    elif t == "user":
        # 工具**结果**回到一条 user 行里（claude 把工具输出当成"用户"喂回给模型），
        # 靠 tool_use_id 关联回上面那条 tool_use。一条 user 行也可能只是普通提示
        # 文本，那就一个事件都不产出——探测器按"整份日志里这个形状有没有产出过
        # 事件"判断，所以不会把这种正常情况误报成异常。
        msg = d.get("message", {})
        for block in msg.get("content", []):
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            events.append(ToolOutcome(
                str(block.get("tool_use_id", "")),
                _claude_tool_result_text(block.get("content")),
                bool(block.get("is_error")),
            ))
    elif t == "usage":
        events.append(UsageDelta(int(d.get("input_tokens", 0)), int(d.get("output_tokens", 0))))
        c = d.get("cost_usd") or d.get("total_cost_usd", 0)
        if c:
            events.append(CostFirstSeen(float(c)))
    return events


def _codex_tool_result_text(result) -> str:
    """
    codex flat tool_result 的 result 有三种编码 → 一段文本。

    第三种（既不是字符串也不是 content 数组，而是个结构化对象，比如
    {"exit_code":0,...}）如果返回空串，就等于把一条真实结果悄悄丢掉了——
    所以序列化成 JSON 而不是放弃。ensure_ascii=False 保证中文输出还是中文。
    """
    if isinstance(result, str):
        return result
    if isinstance(result, list):
        return "\n".join(
            str(c.get("text", "")) for c in result if isinstance(c, dict)
        )
    if result is None:
        return ""
    return json.dumps(result, ensure_ascii=False)


# item.* 生命周期族里算作"工具调用"的 item 类型（issue #77）。
# command_execution 单独处理（它的名字/参数/错误判定都自成一套）；下面这三种
# 走同一条通用路径，只是名字和参数的取法各不相同。
_CODEX_GENERIC_TOOL_ITEMS = ("mcp_tool_call", "web_search", "collab_tool_call")
# 这三个 status 值表示失败，其余（包括缺失）都算成功。
_CODEX_FAILED_STATUSES = frozenset({"cancelled", "error", "failed"})


def _codex_item_tool_name(item: dict) -> str:
    it = item.get("type")
    if it == "web_search":
        return "WebSearch"
    if it == "mcp_tool_call":
        return str(item.get("tool") or "MCP")
    return str(item.get("tool") or "Collab")


def _codex_item_arguments(item: dict) -> dict:
    it = item.get("type")
    if it == "web_search":
        # query 可能直接挂在 item 上，也可能藏在 action 里，而 action 里又分
        # 单值 query 和列表 queries 两种写法——三种都见过，取到哪个算哪个。
        query = item.get("query")
        if isinstance(query, str) and query.strip():
            return {"query": query.strip()}
        action = item.get("action")
        if isinstance(action, dict):
            if action.get("query"):
                return {"query": str(action["query"])}
            queries = action.get("queries")
            if isinstance(queries, list) and queries:
                return {"query": str(queries[0])}
        return {"query": ""}
    if it == "mcp_tool_call":
        return {
            "server": item.get("server"),
            "tool": item.get("tool"),
            "arguments": item.get("arguments"),
        }
    return {
        "tool": item.get("tool"),
        "prompt": item.get("prompt"),
        "agents": item.get("receiver_thread_ids"),
    }


def _codex_item_events(item: dict, completed: bool) -> List[OutputEvent]:
    """
    一条 item.started / item.completed → 零或多个工具事件。

    completed=True 时**总是同时**产出 intent 和 outcome，即使前面已经来过
    item.started。因为 completion 可以在完全没有 started 的情况下直接到达
    （renderer.js 专门为此在找不到卡片时补建一张），而纯函数分类器没法知道
    自己有没有见过那条 started——"见过没有"是渲染器的本地状态，不是这一行
    的内容。产出重复的 intent 由消费方按 call_id 去重，比丢掉一次真实调用
    安全得多。
    """
    it = item.get("type")
    call_id = str(item.get("id", ""))

    if it == "command_execution":
        # command 原样带出。renderer.js 会把 `/bin/bash -lc "..."` 外壳拆掉，
        # 那是显示层的事——真正执行的就是带壳的那条。
        intent = ToolIntent(call_id, "Bash", {"command": str(item.get("command", ""))})
        if not completed:
            return [intent]
        # exit_code 缺失和 exit_code == 0 **都不是失败**，不能混为一谈：
        # `int(item.get("exit_code") or 0)` 之类的写法会把两者压成同一个值，
        # 恰好在这里是对的，但一旦语义反过来（缺失=未知失败）就会静默出错。
        exit_code = item.get("exit_code")
        is_error = exit_code is not None and exit_code != 0
        return [intent, ToolOutcome(call_id, str(item.get("aggregated_output") or ""), is_error)]

    if it in _CODEX_GENERIC_TOOL_ITEMS:
        intent = ToolIntent(call_id, _codex_item_tool_name(item), _codex_item_arguments(item))
        if not completed:
            return [intent]
        # 通用工具没有 exit_code，失败靠 status 判定——同一个格式里两套错误
        # 判定规则，按 item 类型分。
        is_error = str(item.get("status", "")) in _CODEX_FAILED_STATUSES
        # renderer.js 在这里合成的是给人看的中文短句（"已完成网络搜索。"），
        # 那是显示层的措辞，不是数据；这里带出真实结果，没有就留空。
        result = item.get("error") if is_error and item.get("error") is not None else item.get("result")
        return [intent, ToolOutcome(call_id, _codex_tool_result_text(result), is_error)]

    # todo_list 不是工具调用，是计划/进度面板（它也是唯一一种会在 completed
    # 之前反复推 item.updated 快照的 item 类型）；agent_message 走上面的文本
    # 提取；file_change 是文件卡片。三者都不产出工具事件。
    return []


def _events_codex(d: dict) -> List[OutputEvent]:
    t = d.get("type", "")
    if t in ("item.started", "item.completed"):
        item = d.get("item")
        if isinstance(item, dict):
            events = _codex_item_events(item, completed=(t == "item.completed"))
            if events:
                return events
    if t == "tool_call":
        # codex 用**两套完全不同的表示**描述工具活动：这里的扁平
        # tool_call/tool_result 对，以及 item.* 生命周期族（issue #77）。
        # 两套并存，不是新旧替代关系，别把哪一套当成"过时的"删掉。
        #
        # 关联 id 的字段名是**不对称的**：请求行叫 id，结果行叫 tool_call_id。
        # 归一化的就是这个字段名。
        #
        # 请求行没有 id 时，call_id 落成空串——**故意不学 renderer.js 现造一个
        # 随机 id**（它那样做只是为了给 DOM 元素一个 key）。造出来的 id 永远
        # 匹配不上任何结果，等于凭空捏造一条不存在的关联；空串至少诚实地说
        # "这条关联不上"，消费方按空 call_id 一律当作不可关联处理即可。
        return [ToolIntent(
            str(d.get("id", "")),
            str(d.get("name", "") or ""),
            # name/arguments 各有一个备用字段名，是 Codex CLI 版本漂移留下的：
            # 磁盘上的老日志用的是 input。
            _as_arguments_dict(d.get("arguments") if d.get("arguments") is not None else d.get("input")),
        )]
    if t == "tool_result":
        return [ToolOutcome(
            str(d.get("tool_call_id", "")),
            _codex_tool_result_text(d.get("result")),
            bool(d.get("is_error")),
        )]
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


# opencode 的 state.status 里表示"已收尾"和"失败"的取值。completed 算收尾但
# 不算失败；error/failed 两者都算。
_OPENCODE_TERMINAL_STATUSES = frozenset({"completed", "error", "failed"})
_OPENCODE_FAILED_STATUSES = frozenset({"error", "failed"})


def _opencode_tool_events(part: dict) -> List[OutputEvent]:
    """
    一条 opencode tool_use 行 → 工具事件（issue #78）。

    opencode 跟其他所有格式都不一样：**请求和结果共用同一个事件类型**，同一
    个行形状随着调用推进反复出现，区分二者的是嵌在里面的 state.status。

    收尾状态的那一行**总是同时**产出 intent 和 outcome。renderer.js 是靠"这个
    id 我见过没有"来决定要不要补一张请求卡片的，那是渲染器的本地状态，纯函数
    分类器既没有也不该需要。无条件产出两个事件，输出就只取决于这一行本身；
    而且在"running 那行压根没写进日志"的情况下也不会把这次调用整个丢掉。重复
    的 intent 由消费方按 call_id 去重。

    工具名**原样带出，不做规范化**。renderer.js 会把 bash/shell 映射成 Bash
    之类，那是显示层的统一；这套词汇表的 name 承诺的是"这个 CLI 自己报的工具
    名"。把 opencode 的 bash 改写成 claude 的 Bash 会让跨 CLI 对比*看起来*
    整齐，实际却抹掉了"这是两个不同实现"这个真信息——想要规范视图的消费方
    自己映射，反过来则不可能。state.input 同理，不做键名归一。
    """
    state = part.get("state")
    state = state if isinstance(state, dict) else {}
    metadata = state.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}

    call_id = str(part.get("callID") or part.get("id") or "")
    name = str(part.get("tool") or state.get("tool") or "")
    intent = ToolIntent(call_id, name, _as_arguments_dict(state.get("input")))

    status = str(state.get("status", ""))
    if status not in _OPENCODE_TERMINAL_STATUSES:
        return [intent]

    output = state.get("output")
    if output is None:
        output = metadata.get("output")
    # 失败有**两个互相独立**的信号，各自单独成立：终止状态是 error/failed，
    # 或者 metadata.exit 是个非零值。exit 缺失和 exit == 0 都不算失败。
    exit_code = metadata.get("exit")
    is_error = status in _OPENCODE_FAILED_STATUSES or (exit_code is not None and exit_code != 0)
    return [intent, ToolOutcome(call_id, str(output or ""), is_error)]


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

    if t == "tool_use":
        events.extend(_opencode_tool_events(part))
    elif t == "text" and part.get("type") == "text":
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


# ── 行"形状"判别（issue #74）──────────────────────────────────
#
# 覆盖率探测器（log_parser.detect_unrecognized_shapes）要回答的问题是：
# "这个日志里有没有哪一类行，是分类函数从头到尾一个事件都没产出、而我们
# 也没声明过它本来就该沉默的？"——也就是 CLI 悄悄改了输出格式的信号。
# 过去两次静默损坏（#69、#70）都属于这一类，靠人眼盯 renderer.js 才发现。
#
# 要问这个问题就得先能说出"哪一类行"。判别字段**不是统一的**：
# claude/codex/opencode/pi 看 type，kimi 看 role（type 只用来细分 meta）。
# 所以这里不写死字段名，而是每种类型自带一个纯函数把一行映射成形状名，
# 默认取 type。新增类型若判别方式又不一样，改它自己的 shape_of，不要在
# 探测器里加分支。
ShapeFn = Callable[[dict], str]


def _shape_by_type(d: dict) -> str:
    """默认判别：形状名就是 `type` 字段。"""
    return str(d.get("type", ""))


def _shape_kimi(d: dict) -> str:
    """
    kimi 判别：形状名是 `role`，只有 meta 这一类再用 `type` 细分。

    这就是 #74 里坚持"判别字段必须按类型可配"的那个具体理由——kimi 的行
    根本没有顶层 type，用默认判别会把整份日志压成同一个空形状，覆盖率探测
    直接失去意义。改这里，不要去改探测器。
    """
    role = str(d.get("role", ""))
    if role == "meta":
        return f"meta/{d.get('type', '')}"
    return role


# 声明"本来就该沉默"的形状：这些行确实不产生任何 OutputEvent，而且是设计
# 如此，不是漏掉了。少了这份声明，探测器会把每条会话横幅都报成异常，噪音
# 大到没人会看——它的价值完全取决于这份清单是准的。
#
# 起点是 renderer.js 里显式 no-op 的那批 case。下面两条是**超出**那批的额外
# 声明，各有各的理由，加新的也要在这里写清楚为什么：
#   session   —— renderer 会渲染它，但我们这边没人分类；不声明的话每份 pi
#                日志都会永远多报一条，噪音直接淹掉真信号。
#   agent_end —— 见下。
_SILENT_PI = frozenset({
    "session",         # 会话头，只被 _extract_pi 读走 id
    "agent_start", "agent_settled",
    "turn_start", "turn_end",
    "message_start",
    "message_update",  # 逐 delta，完整内容在 message_end
    "tool_execution_update",
    # agent_end 会把本轮所有 message 再列一遍。渲染器读它，我们**故意**不读
    # ——读了就会和 message_end 把同一份文本/用量记两遍。这是有意的沉默。
    "agent_end",
})

# claude 的 switch 里没有一条显式 no-op case——它那五种形状全都会渲染点什么。
# 所以这两条都是"超出 renderer no-op 集"的额外声明，理由各自写在下面。
_SILENT_CLAUDE = frozenset({
    "system",            # subtype=init 的就绪横幅：模型名 + 工具数，没有可归一化的内容
    "rate_limit_event",  # 每次请求都带一份限额快照，绝大多数是 allowed；纯运行时状态，
                         # 不属于任务"产出"的任何一维（文本/用量/工具）
})

# codex 的 renderer switch 里显式 no-op 的是 turn.started / turn.ended，其余
# 几条是超出那批的额外声明，理由写在各自后面。
_SILENT_CODEX = frozenset({
    "turn.started", "turn.ended",
    "thread.started",  # 只被 _extract_codex 读走 thread_id
    "thread.ended",    # 收尾横幅，内容都在 turn.completed 里
    "reasoning",       # 思考过程；不是答复文本，混进 text 会污染工作流取值
    "item.updated",    # 只有 todo_list 会推这个阶段，而 todo_list 不是工具调用
    # CLI 级终止错误（限流/鉴权失败/进程崩溃）。渲染器会显示，我们这边没有
    # 对应的事件种类——错误还不在这套词汇表里，等真需要时再加一个成员，不要
    # 硬塞进 TextChunk。
    "turn.failed", "error",
})

# kimi 的形状名来自 _shape_kimi（role，meta 再按 type 细分），所以这里写的是
# role 值而不是 type 值。
_SILENT_KIMI = frozenset({
    "user",                        # 回放里的用户提示，不是任务产出
    "meta/session.resume_hint",    # 只被 _extract_kimi 读走 session_id
})

_SILENT_OPENCODE = frozenset({
    "step_start",  # 一步的开始横幅，用量都在 step_finish 上
    "reasoning",   # 同 codex：思考过程不是答复文本
})


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
    shape_of: ShapeFn = _shape_by_type
    # 把一行映射成"形状名"的纯函数，供覆盖率探测器分组用。默认取 type；判别
    # 字段不同的类型（如 kimi 看 role）在这里换掉，不要去改探测器。
    # 必须对任意 dict 全函数（只读字段 + 转字符串，不做类型假设）：探测器
    # 不给它兜底——没有形状名就连"这行炸了"都报不出来。分类函数本身允许
    # 抛（探测器会把抛异常当作"没识别"记账），shape_of 不允许。
    silent_shapes: frozenset = frozenset()
    # 声明"确实不产出任何事件、且设计如此"的形状名。探测器只报既没产出事件、
    # 又不在这份清单里的形状。parse_output_events 为 None 时本字段无意义。


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
        silent_shapes=_SILENT_CLAUDE,
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
        silent_shapes=_SILENT_CODEX,
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
        silent_shapes=_SILENT_OPENCODE,
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
        shape_of=_shape_kimi,
        silent_shapes=_SILENT_KIMI,
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
        silent_shapes=_SILENT_PI,
    ),
}


# ── 公开 API ──────────────────────────────────────────────────

def get_spec(type_id: str) -> AccountTypeSpec:
    """返回账号类型的 spec，未知类型抛出 KeyError。"""
    return ACCOUNT_TYPES[type_id]


@dataclass
class LocalCliDetection:
    """本地 CLI 探测结果：runtime=local 之前用来回答"这台宿主机装没装这个 CLI"。

    只回答二进制存不存在、版本是什么 —— 不做登录态检查，那是 Account 凭证目录
    （Scheduler.local_auth_dir）的职责，跟"CLI 装没装"是两个独立的问题。
    """
    type_id: str
    binary:  str
    found:   bool
    path:    str = ""
    version: str = ""


def detect_local_cli(type_id: str) -> LocalCliDetection:
    """探测 type_id 对应的 CLI 二进制是否已安装在宿主机上。

    CLI（`coderfleet account detect`）和 Web UI（`/api/accounts/detect-local`）
    共用这一个函数 —— 避免出现两份探测逻辑各自漂移。login_cli 已经是
    AccountTypeSpec 里"这个账号类型对应哪个二进制"的唯一事实来源，探测直接
    复用它，不新增第二份绑定。
    """
    import shutil
    import subprocess

    spec = get_spec(type_id)
    binary = spec.login_cli
    path = shutil.which(binary)
    if not path:
        return LocalCliDetection(type_id=type_id, binary=binary, found=False)

    version = ""
    try:
        result = subprocess.run(
            [path, "--version"], capture_output=True, text=True, timeout=10,
        )
        raw = (result.stdout or result.stderr or "").strip()
        if raw:
            version = raw.splitlines()[0].strip()
    except Exception:
        pass
    return LocalCliDetection(type_id=type_id, binary=binary, found=True, path=path, version=version)


def detect_all_local_clis() -> list[LocalCliDetection]:
    """对所有已注册账号类型各探测一次。"""
    return [detect_local_cli(t) for t in valid_type_ids()]


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
