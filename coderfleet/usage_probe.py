"""
usage_probe.py — Claude Max / ChatGPT (Codex) 套餐用量探测

Claude Code CLI 和 Codex CLI 都在账号目录下自己持有一份 OAuth access token。
usage 接口必须从账号本身惯用的网络路径打出去——也就是账号所在容器（会经过
gost 代理中继），不能从宿主机直连。宿主机的出口 IP/网络环境跟账号平时的流量
路径不一样，同一个账号的请求一会儿走代理一会儿裸连，容易被判定异常触发风控
（这也是这套 coderfleet 本身把所有容器流量都强制走 gost 中继的原因）。

所以这里不在宿主机发 HTTP 请求，而是 docker exec 进账号当前正在使用的容器，
用容器里现成的 python3（走容器自己的 HTTP_PROXY/HTTPS_PROXY 环境变量）去发
这个请求，宿主机只负责把 docker exec 的 stdout 解析成结构化结果。

token 过期时不做自己的刷新——账号一旦被真实任务用到，CLI 自会刷新凭据文件，
我们只负责"读"。
"""
from __future__ import annotations

import asyncio
import json
import subprocess
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel

# ── Claude ───────────────────────────────────────────────────
USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
OAUTH_BETA_HEADER = "oauth-2025-04-20"
DEFAULT_CLI_VERSION = "2.1.197"
CRED_PATH_IN_CONTAINER = "/home/byclaw/.claude/.credentials.json"

# ── Codex ────────────────────────────────────────────────────
CODEX_USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
DEFAULT_CODEX_CLI_VERSION = "0.142.5"
CODEX_CRED_PATH_IN_CONTAINER = "/home/byclaw/.codex/auth.json"

PROBE_TIMEOUT_SECONDS = 25.0


class UsageWindow(BaseModel):
    utilization: Optional[float] = None
    resets_at: Optional[str] = None


class AccountUsage(BaseModel):
    five_hour:        Optional[UsageWindow] = None
    seven_day:        Optional[UsageWindow] = None
    seven_day_opus:   Optional[UsageWindow] = None
    seven_day_sonnet: Optional[UsageWindow] = None
    subscription_type: str = ""
    fetched_at:       str = ""
    error:            str = ""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _exec_argv(container_name: str, script: str) -> list[str]:
    return ["docker", "exec", container_name, "python3", "-c", script]


def _run_probe_script_sync(container_name: str, script: str, now: str) -> tuple[Optional[str], Optional[AccountUsage]]:
    """跑探测脚本，返回 (stdout, None) 表示成功执行（还需再解析 stdout），
    或 (None, AccountUsage) 表示连 docker exec 本身都没跑起来。"""
    try:
        result = subprocess.run(
            _exec_argv(container_name, script),
            capture_output=True, text=True, timeout=PROBE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return None, AccountUsage(fetched_at=now, error="probe_timeout")
    except OSError as e:
        return None, AccountUsage(fetched_at=now, error=f"docker_exec_error: {e}")
    return result.stdout, None


async def _run_probe_script_async(container_name: str, script: str, now: str) -> tuple[Optional[str], Optional[AccountUsage]]:
    try:
        proc = await asyncio.create_subprocess_exec(
            *_exec_argv(container_name, script),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=PROBE_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        return None, AccountUsage(fetched_at=now, error="probe_timeout")
    except OSError as e:
        return None, AccountUsage(fetched_at=now, error=f"docker_exec_error: {e}")
    return stdout.decode(errors="ignore"), None


def _last_json_line(stdout: str) -> Optional[dict]:
    lines = [l for l in stdout.splitlines() if l.strip()]
    if not lines:
        return None
    try:
        return json.loads(lines[-1])
    except json.JSONDecodeError:
        return None


# ── Claude: OAuth usage endpoint ─────────────────────────────
#
# 在容器内跑的探测脚本：读凭据 -> urllib 直接发请求（自动遵循容器的
# HTTP_PROXY/HTTPS_PROXY 环境变量）-> 打一行 JSON 到 stdout。
# 用 urllib（标准库）而不是 curl，避免 header 和 body 混在一起不好解析。
_CLAUDE_PROBE_SCRIPT = """
import json, sys, urllib.request, urllib.error
try:
    with open(%(cred_path)r) as f:
        data = json.load(f)
    oauth = data["claudeAiOauth"]
    token = oauth["accessToken"]
    subscription = oauth.get("subscriptionType", "")
except Exception:
    print(json.dumps({"error": "no_credentials"}))
    sys.exit(0)

req = urllib.request.Request(
    %(url)r,
    headers={
        "Authorization": "Bearer " + token,
        "Accept": "application/json",
        "Content-Type": "application/json",
        "anthropic-beta": %(beta)r,
        "User-Agent": "claude-code/%(version)s",
    },
)
try:
    with urllib.request.urlopen(req, timeout=15) as resp:
        print(json.dumps({"status": resp.status, "body": resp.read().decode(), "subscription": subscription}))
except urllib.error.HTTPError as e:
    print(json.dumps({"status": e.code, "body": e.read().decode(errors="ignore"), "subscription": subscription}))
except Exception as e:
    print(json.dumps({"error": "network_error", "detail": str(e)}))
"""


def _render_claude_probe_script(cli_version: str) -> str:
    return _CLAUDE_PROBE_SCRIPT % {
        "cred_path": CRED_PATH_IN_CONTAINER,
        "url": USAGE_URL,
        "beta": OAUTH_BETA_HEADER,
        "version": cli_version,
    }


def _parse_claude_window(raw: Optional[dict]) -> Optional[UsageWindow]:
    if not raw:
        return None
    return UsageWindow(utilization=raw.get("utilization"), resets_at=raw.get("resets_at"))


def _claude_usage_from_status(status_code, body: str, subscription: str, now: str) -> AccountUsage:
    if status_code == 401:
        return AccountUsage(fetched_at=now, error="unauthorized")
    if status_code == 429:
        return AccountUsage(fetched_at=now, error="rate_limited")
    if status_code != 200:
        return AccountUsage(fetched_at=now, error=f"http_{status_code}")
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        return AccountUsage(fetched_at=now, error="invalid_json")
    return AccountUsage(
        five_hour         = _parse_claude_window(data.get("five_hour")),
        seven_day         = _parse_claude_window(data.get("seven_day")),
        seven_day_opus    = _parse_claude_window(data.get("seven_day_opus")),
        seven_day_sonnet  = _parse_claude_window(data.get("seven_day_sonnet")),
        subscription_type = subscription,
        fetched_at        = now,
    )


def _parse_claude_probe_stdout(stdout: str, now: str) -> AccountUsage:
    payload = _last_json_line(stdout)
    if payload is None:
        return AccountUsage(fetched_at=now, error="empty_response" if not stdout.strip() else "invalid_json")
    if "error" in payload:
        return AccountUsage(fetched_at=now, error=payload["error"])
    return _claude_usage_from_status(
        payload.get("status"), payload.get("body", ""), payload.get("subscription", ""), now,
    )


def probe_via_container(container_name: str, cli_version: str = DEFAULT_CLI_VERSION) -> AccountUsage:
    """同步探测 Claude 账号用量（CLI 用）。永不抛异常。"""
    now = now_iso()
    stdout, err = _run_probe_script_sync(container_name, _render_claude_probe_script(cli_version), now)
    if err is not None:
        return err
    return _parse_claude_probe_stdout(stdout, now)


async def probe_via_container_async(container_name: str, cli_version: str = DEFAULT_CLI_VERSION) -> AccountUsage:
    """异步探测 Claude 账号用量（scheduler 轮询用）。永不抛异常。"""
    now = now_iso()
    stdout, err = await _run_probe_script_async(container_name, _render_claude_probe_script(cli_version), now)
    if err is not None:
        return err
    return _parse_claude_probe_stdout(stdout, now)


# ── Codex: ChatGPT backend usage endpoint ────────────────────
#
# 凭据文件是 $CODEX_HOME/auth.json（默认 ~/.codex/auth.json），结构：
#   {"tokens": {"access_token": ..., "account_id": ...}, "last_refresh": ...}
# 接口 rate_limit.primary_window / secondary_window 分别对应 5 小时 / 7 天窗口，
# 跟 Claude 的 five_hour / seven_day 概念一一对应，所以复用同一个 AccountUsage 结构。
_CODEX_PROBE_SCRIPT = """
import json, sys, urllib.request, urllib.error
try:
    with open(%(cred_path)r) as f:
        data = json.load(f)
    tokens = data.get("tokens") or {}
    token = tokens.get("access_token")
    account_id = tokens.get("account_id")
    if not token:
        raise KeyError("access_token")
except Exception:
    print(json.dumps({"error": "no_credentials"}))
    sys.exit(0)

headers = {
    "Authorization": "Bearer " + token,
    "Accept": "application/json",
    "User-Agent": "codex_cli_rs/%(version)s",
}
if account_id:
    headers["ChatGPT-Account-Id"] = account_id

req = urllib.request.Request(%(url)r, headers=headers)
try:
    with urllib.request.urlopen(req, timeout=15) as resp:
        print(json.dumps({"status": resp.status, "body": resp.read().decode()}))
except urllib.error.HTTPError as e:
    print(json.dumps({"status": e.code, "body": e.read().decode(errors="ignore")}))
except Exception as e:
    print(json.dumps({"error": "network_error", "detail": str(e)}))
"""


def _render_codex_probe_script(cli_version: str) -> str:
    return _CODEX_PROBE_SCRIPT % {
        "cred_path": CODEX_CRED_PATH_IN_CONTAINER,
        "url": CODEX_USAGE_URL,
        "version": cli_version,
    }


def _parse_codex_window(raw: Optional[dict]) -> Optional[UsageWindow]:
    if not raw:
        return None
    reset_at = raw.get("reset_at")
    resets_at_iso = None
    if isinstance(reset_at, (int, float)):
        resets_at_iso = datetime.fromtimestamp(reset_at, tz=timezone.utc).isoformat()
    return UsageWindow(utilization=raw.get("used_percent"), resets_at=resets_at_iso)


def _codex_usage_from_status(status_code, body: str, now: str) -> AccountUsage:
    if status_code in (401, 403):
        return AccountUsage(fetched_at=now, error="unauthorized")
    if status_code == 429:
        return AccountUsage(fetched_at=now, error="rate_limited")
    if status_code != 200:
        return AccountUsage(fetched_at=now, error=f"http_{status_code}")
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        return AccountUsage(fetched_at=now, error="invalid_json")
    rate_limit = data.get("rate_limit") or {}
    return AccountUsage(
        five_hour         = _parse_codex_window(rate_limit.get("primary_window")),
        seven_day         = _parse_codex_window(rate_limit.get("secondary_window")),
        subscription_type = data.get("plan_type") or "",
        fetched_at        = now,
    )


def _parse_codex_probe_stdout(stdout: str, now: str) -> AccountUsage:
    payload = _last_json_line(stdout)
    if payload is None:
        return AccountUsage(fetched_at=now, error="empty_response" if not stdout.strip() else "invalid_json")
    if "error" in payload:
        return AccountUsage(fetched_at=now, error=payload["error"])
    return _codex_usage_from_status(payload.get("status"), payload.get("body", ""), now)


def probe_codex_via_container(container_name: str, cli_version: str = DEFAULT_CODEX_CLI_VERSION) -> AccountUsage:
    """同步探测 Codex 账号用量（CLI 用）。永不抛异常。"""
    now = now_iso()
    stdout, err = _run_probe_script_sync(container_name, _render_codex_probe_script(cli_version), now)
    if err is not None:
        return err
    return _parse_codex_probe_stdout(stdout, now)


async def probe_codex_via_container_async(container_name: str, cli_version: str = DEFAULT_CODEX_CLI_VERSION) -> AccountUsage:
    """异步探测 Codex 账号用量（scheduler 轮询用）。永不抛异常。"""
    now = now_iso()
    stdout, err = await _run_probe_script_async(container_name, _render_codex_probe_script(cli_version), now)
    if err is not None:
        return err
    return _parse_codex_probe_stdout(stdout, now)
