"""
login_cmd.py — coderfleet login 命令

交互式账号登录，通过 docker exec（已运行容器）或 docker run（临时容器）
执行 claude login / codex login --device-auth / opencode auth login。

关键技术：os.execvp() 替换当前进程，确保 TTY 完整继承。
当 login all 时改用 subprocess.run() 以便在循环中逐一执行。
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import click

from coderfleet.config import load_config, parse_conf


# ── helpers ───────────────────────────────────────────────────


def _is_running(container: str) -> bool:
    r = subprocess.run(
        ["docker", "inspect", container, "--format", "{{.State.Running}}"],
        capture_output=True, text=True,
    )
    return r.stdout.strip() == "true"


def _cli_and_args(acc_type: str) -> tuple[str, list[str]]:
    """Return (cli_binary, login_subcommand_args) for the given account type."""
    if acc_type == "claude":
        return "claude", ["login"]
    if acc_type == "opencode":
        return "opencode", ["auth", "login"]
    if acc_type == "hermes":
        return "hermes", ["setup"]
    return "codex", ["login", "--device-auth"]


def _auth_mount_dst(acc_type: str) -> str:
    if acc_type == "codex":
        return "/home/byclaw/.codex"
    if acc_type == "claude":
        return "/home/byclaw/.claude"
    if acc_type == "opencode":
        return "/home/byclaw/.opencode"
    if acc_type == "hermes":
        return "/home/byclaw/.hermes"
    return "/home/byclaw/.codex"


def _login_single(ws: Path, target: str, *, replace_process: bool = True) -> int:
    """
    Login one account.
    replace_process=True  → os.execvp (never returns, proper TTY for single login)
    replace_process=False → subprocess.run (returns exit code, used by 'all' loop)
    """
    accounts = {r["NAME"]: r for r in parse_conf(ws / "accounts.conf") if "NAME" in r}
    if target not in accounts:
        raise click.ClickException(f"账号 '{target}' 不在 accounts.conf 中")

    acc = accounts[target]
    acc_type = acc.get("TYPE", "")
    auth = acc.get("AUTH", "login")
    env_file = acc.get("ENV_FILE", "")

    if auth == "env":
        click.secho(
            f"账号 {target} 使用环境变量认证（ENV_FILE={env_file}），无需交互登录",
            fg="cyan",
        )
        click.secho(
            "CLI 会优先使用环境变量中的 API Key，可能按 API 计费；"
            "请用 /status 确认认证方式",
            fg="yellow",
        )
        return 0

    cli, login_args = _cli_and_args(acc_type)

    # Find a running project container for this account
    running_ctr: str | None = None
    for p in parse_conf(ws / "projects.conf"):
        if p.get("ACCOUNT") != target:
            continue
        pname = p.get("NAME", "")
        ctr = f"{acc_type}-{pname}"
        if _is_running(ctr):
            running_ctr = ctr
            break

    click.echo(f"登录账号 {target}（类型：{acc_type}）...")
    click.secho("  提示：浏览器打开授权 URL → 复制 code → 粘贴回来", fg="yellow")
    click.echo()

    if running_ctr:
        cmd = ["docker", "exec", "-it", running_ctr, cli] + login_args
        if replace_process:
            os.execvp(cmd[0], cmd)
        return subprocess.run(cmd).returncode

    # No running container → start a temporary one
    cfg = load_config(ws)
    image = f"{cfg.get('IMAGE_NAME', 'coderfleet')}:{cfg.get('IMAGE_TAG', 'latest')}"
    platform = cfg.get("BUILD_PLATFORM", "linux/amd64")
    proxy_host = cfg.get("PROXY_HOST", "host.docker.internal")
    proxy_port = cfg.get("PROXY_HTTP_PORT", "7890")
    proxy_url = f"http://{proxy_host}:{proxy_port}"
    auth_dst = _auth_mount_dst(acc_type)
    auth_src = str(ws / "accounts" / target)
    login_ctr = f"coderfleet-login-{acc_type}-{target}"

    r = subprocess.run(["docker", "image", "inspect", image], capture_output=True)
    if r.returncode != 0:
        raise click.ClickException(f"镜像 {image} 不存在，请先执行：coderfleet build")

    Path(auth_src).mkdir(parents=True, exist_ok=True)
    subprocess.run(["docker", "rm", "-f", login_ctr], capture_output=True)

    click.secho(f"账号容器未运行，启动临时认证容器 {login_ctr}...", fg="cyan")

    cmd = [
        "docker", "run", "--rm", "-it",
        "--name", login_ctr,
        "--platform", platform,
        "--add-host", "host.docker.internal:host-gateway",
        "-e", f"HTTP_PROXY={proxy_url}",
        "-e", f"HTTPS_PROXY={proxy_url}",
        "-e", f"http_proxy={proxy_url}",
        "-e", f"https_proxy={proxy_url}",
        "-e", f"ALL_PROXY={proxy_url}",
        "-e", f"all_proxy={proxy_url}",
        "-e", "NO_PROXY=localhost,127.0.0.1",
        "-e", "no_proxy=localhost,127.0.0.1",
        "-e", "CODEX_HOME=/home/byclaw/.codex",
        "-e", "CLAUDE_CONFIG_DIR=/home/byclaw/.claude",
        "-e", f"CODERFLEET_ACCOUNT_NAME={target}",
        "-e", f"CODERFLEET_ACCOUNT_TYPE={acc_type}",
        "-e", f"CODERFLEET_RELAY_IP={proxy_host}",
        "-e", f"CODERFLEET_RELAY_PORT={proxy_port}",
        "-v", f"{auth_src}:{auth_dst}",
        "-w", "/workspace",
        image, cli,
    ] + login_args

    if acc_type == "opencode":
        insert_at = cmd.index("-v")
        cmd[insert_at:insert_at] = [
            "-e", "XDG_DATA_HOME=/home/byclaw/.opencode/data",
            "-e", "XDG_CONFIG_HOME=/home/byclaw/.opencode/config",
            "-e", "XDG_STATE_HOME=/home/byclaw/.opencode/state",
            "-e", "XDG_CACHE_HOME=/home/byclaw/.opencode/cache",
        ]

    if acc_type == "hermes":
        insert_at = cmd.index("-v")
        cmd[insert_at:insert_at] = [
            "-e", "HERMES_HOME=/home/byclaw/.hermes",
        ]

    if replace_process:
        os.execvp(cmd[0], cmd)
    return subprocess.run(cmd).returncode


# ── Click command ─────────────────────────────────────────────


@click.command("login")
@click.argument("target", metavar="<account|all>")
@click.pass_context
def cmd_login(ctx: click.Context, target: str) -> None:
    """Login an account (or all accounts) interactively.

    Uses an existing project container if one is running, otherwise
    starts a temporary container for the login flow.
    """
    ws: Path = ctx.obj["workspace"]

    if target != "all":
        _login_single(ws, target, replace_process=True)
        return

    accounts = [r for r in parse_conf(ws / "accounts.conf") if "NAME" in r]
    if not accounts:
        raise click.ClickException("accounts.conf 中没有账号")

    total = len(accounts)
    for i, acc in enumerate(accounts, 1):
        name = acc["NAME"]
        click.echo()
        click.secho(f"  ── [{i}/{total}] 账号：{name} " + "─" * 30, bold=True)
        _login_single(ws, name, replace_process=False)

    click.echo()
    click.secho("✓ 所有账号登录完成", fg="green")
