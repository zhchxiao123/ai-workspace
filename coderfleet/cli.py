from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
import click
from pathlib import Path

from coderfleet.config import get_workspace, load_config


# ── server daemon helpers ──────────────────────────────────────


def _pid_file(ws: Path) -> Path:
    return ws / "server.pid"


def _log_file(ws: Path) -> Path:
    return ws / "server.log"


def _is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _read_pid(ws: Path) -> int | None:
    p = _pid_file(ws)
    if not p.exists():
        return None
    try:
        return int(p.read_text().strip())
    except ValueError:
        return None


def _server_start_daemon(ws: Path, port: int) -> None:
    pid = _read_pid(ws)
    if pid and _is_running(pid):
        click.secho(f"server 已在运行（PID {pid}）", fg="yellow")
        click.echo(f"  日志：{_log_file(ws)}")
        click.echo(f"  停止：coderfleet server --stop")
        return

    log = _log_file(ws)
    cmd = [sys.executable, "-m", "coderfleet", "server", "--port", str(port)]

    with log.open("a") as fh:
        proc = subprocess.Popen(
            cmd,
            stdout=fh,
            stderr=fh,
            stdin=subprocess.DEVNULL,
            start_new_session=True,          # 脱离当前终端会话
            env={**os.environ, "CODERFLEET_WORKSPACE": str(ws), "CODERFLEET_PORT": str(port)},
        )

    _pid_file(ws).write_text(str(proc.pid))

    # 短暂等待确认进程存活
    time.sleep(1)
    if _is_running(proc.pid):
        click.secho(f"✓ server 已在后台启动（PID {proc.pid}）", fg="green")
        click.echo(f"  Web UI：http://localhost:{port}")
        click.echo(f"  日志：{log}")
        click.echo(f"  停止：coderfleet server --stop")
    else:
        _pid_file(ws).unlink(missing_ok=True)
        raise click.ClickException(f"server 启动失败，查看日志：{log}")


def _server_stop(ws: Path) -> None:
    pid = _read_pid(ws)
    if pid is None:
        click.secho("server 未在运行（找不到 PID 文件）", fg="yellow")
        return
    if not _is_running(pid):
        _pid_file(ws).unlink(missing_ok=True)
        click.secho("server 未在运行（PID 文件已清理）", fg="yellow")
        return

    os.kill(pid, signal.SIGTERM)
    for _ in range(20):          # 最多等 10 秒
        time.sleep(0.5)
        if not _is_running(pid):
            break
    else:
        os.kill(pid, signal.SIGKILL)

    _pid_file(ws).unlink(missing_ok=True)
    click.secho(f"✓ server 已停止（PID {pid}）", fg="green")


def _server_status(ws: Path) -> None:
    pid = _read_pid(ws)
    if pid and _is_running(pid):
        click.secho(f"● server 运行中（PID {pid}）", fg="green")
        click.echo(f"  日志：{_log_file(ws)}")
    else:
        if pid:
            _pid_file(ws).unlink(missing_ok=True)
        click.secho("○ server 未运行", fg="yellow")


@click.group()
@click.pass_context
def main(ctx: click.Context) -> None:
    """CoderFleet

    Manages multiple Claude Code / Codex / OpenCode accounts in isolated Docker containers.

    Workspace location: CODERFLEET_WORKSPACE env var, or ~/.coderfleet/ by default.
    """
    ctx.ensure_object(dict)
    ctx.obj["workspace"] = get_workspace()


from coderfleet.task_cmds import task_group
from coderfleet.config_cmds import account_group, project_group
from coderfleet.docker_ops import (
    cmd_build, cmd_apply, cmd_up, cmd_down,
    cmd_restart, cmd_status, cmd_logs, cmd_enter, cmd_check_proxy,
)
from coderfleet.login_cmd import cmd_login

main.add_command(task_group)
main.add_command(account_group)
main.add_command(project_group)
main.add_command(cmd_build)
main.add_command(cmd_apply)
main.add_command(cmd_up)
main.add_command(cmd_down)
main.add_command(cmd_restart)
main.add_command(cmd_status)
main.add_command(cmd_logs)
main.add_command(cmd_enter)
main.add_command(cmd_check_proxy)
main.add_command(cmd_login)


@main.command("init")
@click.pass_context
def cmd_init(ctx: click.Context) -> None:
    """Initialize the CoderFleet workspace (interactive setup wizard)."""
    from coderfleet.init_wizard import run_init_wizard
    run_init_wizard(ctx.obj["workspace"])


@main.command("server")
@click.option("--port", default=None, type=int, envvar="CODERFLEET_PORT",
              help="监听端口（默认 8765）")
@click.option("--daemon", "-d", is_flag=True,
              help="后台守护进程模式运行")
@click.option("--stop", "do_stop", is_flag=True,
              help="停止后台运行的 server")
@click.option("--status", "do_status", is_flag=True,
              help="查看 server 运行状态")
@click.pass_context
def cmd_server(
    ctx: click.Context,
    port: int | None,
    daemon: bool,
    do_stop: bool,
    do_status: bool,
) -> None:
    """Start the FastAPI scheduler server and Web UI.

    \b
    coderfleet server              # 前台运行（Ctrl+C 停止）
    coderfleet server --daemon     # 后台守护进程
    coderfleet server --stop       # 停止后台 server
    coderfleet server --status     # 查看运行状态
    """
    import uvicorn

    ws = ctx.obj["workspace"]

    if do_stop:
        _server_stop(ws)
        return

    if do_status:
        _server_status(ws)
        return

    cfg = load_config(ws)
    resolved_port = port or int(cfg.get("CODERFLEET_PORT", 8765))

    if daemon:
        _server_start_daemon(ws, resolved_port)
        return

    # 前台模式
    os.environ["CODERFLEET_WORKSPACE"] = str(ws)
    os.environ["CODERFLEET_PORT"] = str(resolved_port)

    click.echo(f"Workspace: {ws}")
    click.echo(f"Starting CoderFleet server at http://localhost:{resolved_port}")

    uvicorn.run(
        "coderfleet.server.main:app",
        host="0.0.0.0",
        port=resolved_port,
        reload=False,
        workers=1,
    )
