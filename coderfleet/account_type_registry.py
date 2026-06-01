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
from typing import Callable, List


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

InnerCmdFn = Callable[[str, bool, str, str, str, str, List[str]], str]
ExtractFn  = Callable[[str], str]


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


def _build_claude(prompt, auto, task_id, marker, task_env, session_id, images):
    p  = f"{prompt}\n\n[Attached images:\n" + "\n".join(images) + "]" if images else prompt
    ep = shlex.quote(p)
    perm   = "--dangerously-skip-permissions" if auto else "--permission-mode acceptEdits"
    resume = f" --resume {shlex.quote(session_id)}" if session_id else ""
    sys_hint = shlex.quote(_CF_SEND_SYSTEM_HINT)
    return (
        f"printf '%s\\n' {ep} | "
        f"CODERFLEET_TASK_ID={task_env} exec -a {marker} "
        f"claude -p {perm} --output-format stream-json --verbose"
        f" --append-system-prompt {sys_hint}{resume}"
    )


def _build_codex(prompt, auto, task_id, marker, task_env, session_id, images):
    ep   = shlex.quote(prompt)
    imgs = "".join(f" -i {shlex.quote(i)}" for i in images)
    if session_id:
        danger = " --dangerously-bypass-approvals-and-sandbox" if auto else ""
        return (
            f"printf '%s\\n' {ep} | "
            f"CODERFLEET_TASK_ID={task_env} exec -a {marker} "
            f"codex exec resume {shlex.quote(session_id)} --json{danger}{imgs}"
        )
    sandbox = "danger-full-access" if auto else "workspace-write"
    return (
        f"printf '%s\\n' {ep} | "
        f"CODERFLEET_TASK_ID={task_env} exec -a {marker} "
        f"codex exec --json --sandbox {sandbox}{imgs}"
    )


def _build_opencode(prompt, auto, task_id, marker, task_env, session_id, images):
    ep    = shlex.quote(prompt)
    perm  = " --dangerously-skip-permissions" if auto else ""
    sess  = f" --session {shlex.quote(session_id)}" if session_id else ""
    files = "".join(f" --file {shlex.quote(i)}" for i in images)
    return (
        f"CODERFLEET_TASK_ID={task_env} exec -a {marker} "
        f"opencode run --format json{perm}{sess}{files} {ep}"
    )


def _build_hermes(prompt, auto, task_id, marker, task_env, session_id, images):
    ep     = shlex.quote(prompt)
    yolo   = " --yolo" if auto else ""
    resume = f" --resume {shlex.quote(session_id)}" if session_id else ""
    return (
        f"CODERFLEET_TASK_ID={task_env} exec -a {marker} "
        f"/opt/hermes-venv/bin/hermes{resume} chat -q {ep}{yolo}"
    )


def _build_grok(prompt, auto, task_id, marker, task_env, session_id, images):
    p    = f"{prompt}\n\n[Attached images:\n" + "\n".join(images) + "]" if images else prompt
    ep   = shlex.quote(p)
    sid  = session_id if session_id else task_id
    qsid = shlex.quote(sid)
    approve = " --always-approve" if auto else ""
    # printf 先写 session_id 标记行到日志，再 exec 替换为 grok 进程
    return (
        f"printf 'grok_session_id=%s\\n' {qsid} && "
        f"CODERFLEET_TASK_ID={task_env} exec -a {marker} "
        f"grok -p {ep} -s {qsid}{approve} --no-auto-update --output-format streaming-json"
    )


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
        if d.get("type") == "end" and d.get("sessionId"):
            return str(d["sessionId"])
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


# ── AccountType 枚举（从注册表自动派生，勿手动编辑）───────────
# 与 class AccountType(str, Enum) 等价，支持 AccountType.claude 等属性访问。

AccountType = Enum("AccountType", {k: k for k in ACCOUNT_TYPES}, type=str)
