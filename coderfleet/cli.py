from __future__ import annotations

import os
import sys
import click
from pathlib import Path

from coderfleet.config import get_workspace, load_config
from coderfleet import server_daemon as sd


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
from coderfleet.schedule_cmds import schedule_group, cmd_digest
from coderfleet.config_cmds import account_group, config_group, project_group
from coderfleet.docker_ops import (
    cmd_build, cmd_apply, cmd_up, cmd_down,
    cmd_restart, cmd_status, cmd_logs, cmd_enter, cmd_check_proxy,
)
from coderfleet.login_cmd import cmd_login

main.add_command(task_group)
main.add_command(schedule_group)
main.add_command(cmd_digest)
main.add_command(account_group)
main.add_command(project_group)
main.add_command(config_group)
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


# ── server ─────────────────────────────────────────────────────────────────────

@main.command("server")
@click.option("--port", default=None, type=int, envvar="CODERFLEET_PORT",
              help="监听端口（默认 8765）")
@click.option("--daemon", "-d", is_flag=True, help="后台守护进程模式运行")
@click.option("--stop", "do_stop", is_flag=True, help="停止后台运行的 server")
@click.option("--status", "do_status", is_flag=True, help="查看 server 运行状态")
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
        stopped = sd.stop_daemon(ws)
        if stopped:
            click.secho("✓ server 已停止", fg="green")
        else:
            click.secho("server 未在运行", fg="yellow")
        return

    if do_status:
        pid = sd.read_pid(ws)
        if pid and sd.is_running(pid):
            click.secho(f"● server 运行中（PID {pid}）", fg="green")
            click.echo(f"  日志：{sd.log_file(ws)}")
        else:
            if pid:
                sd.pid_file(ws).unlink(missing_ok=True)
            click.secho("○ server 未运行", fg="yellow")
        return

    cfg = load_config(ws)
    resolved_port = port or int(cfg.get("CODERFLEET_PORT", 8765))

    if daemon:
        try:
            pid = sd.start_daemon(ws, resolved_port)
            click.secho(f"✓ server 已在后台启动（PID {pid}）", fg="green")
            click.echo(f"  Web UI：http://localhost:{resolved_port}")
            click.echo(f"  日志：{sd.log_file(ws)}")
            click.echo(f"  停止：coderfleet server --stop")
        except RuntimeError as e:
            raise click.ClickException(str(e))
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


# ── tray ───────────────────────────────────────────────────────────────────────

@main.group("tray", invoke_without_command=True)
@click.pass_context
def cmd_tray(ctx: click.Context) -> None:
    """System-tray app — manages server lifecycle and sends task notifications.

    macOS: menu-bar (rumps).  Windows/Linux: system tray (pystray).

    \b
    coderfleet tray            # run tray in foreground
    coderfleet tray install    # (macOS) install LaunchAgent (auto-start at login)
    coderfleet tray uninstall  # (macOS) remove LaunchAgent + stop tray
    coderfleet tray start      # (macOS) start tray via launchctl
    coderfleet tray stop       # (macOS) stop tray (server keeps running)
    """
    if ctx.invoked_subcommand is None:
        from coderfleet.tray import run_tray
        run_tray()


@cmd_tray.command("install")
@click.pass_context
def tray_install(ctx: click.Context) -> None:
    """Install LaunchAgent so the tray auto-starts at login, then start it now."""
    if sys.platform != "darwin":
        raise click.ClickException("LaunchAgent is macOS-only.")
    from coderfleet.tray import install_launchagent, launchagent_installed, _LAUNCHAGENT_PLIST
    install_launchagent()
    click.secho(f"✓ LaunchAgent installed: {_LAUNCHAGENT_PLIST}", fg="green")
    click.echo("  Tray will auto-start at login and restart on crash.")
    click.echo(f"  Stop:      coderfleet tray stop")
    click.echo(f"  Uninstall: coderfleet tray uninstall")


@cmd_tray.command("uninstall")
def tray_uninstall() -> None:
    """Remove LaunchAgent and stop the tray process."""
    if sys.platform != "darwin":
        raise click.ClickException("LaunchAgent is macOS-only.")
    from coderfleet.tray import uninstall_launchagent, _LAUNCHAGENT_PLIST, launchagent_installed
    if not launchagent_installed():
        click.secho("LaunchAgent is not installed.", fg="yellow")
        return
    uninstall_launchagent()
    click.secho(f"✓ LaunchAgent removed ({_LAUNCHAGENT_PLIST})", fg="green")
    click.echo("  Server continues running. Stop it with: coderfleet server --stop")


@cmd_tray.command("start")
def tray_start() -> None:
    """Start the tray via launchctl (requires install first)."""
    if sys.platform != "darwin":
        raise click.ClickException("macOS-only.")
    from coderfleet.tray import launchagent_ctl, launchagent_installed
    if not launchagent_installed():
        raise click.ClickException("LaunchAgent not installed. Run: coderfleet tray install")
    launchagent_ctl("start")
    click.secho("✓ Tray started", fg="green")


@cmd_tray.command("stop")
def tray_stop() -> None:
    """Stop the tray process (server keeps running)."""
    if sys.platform != "darwin":
        raise click.ClickException("macOS-only.")
    from coderfleet.tray import launchagent_ctl, launchagent_installed
    if not launchagent_installed():
        raise click.ClickException("LaunchAgent not installed. Run: coderfleet tray install")
    launchagent_ctl("stop")
    click.secho("✓ Tray stopped (server still running)", fg="green")


@cmd_tray.command("status")
@click.pass_context
def tray_status(ctx: click.Context) -> None:
    """Show tray and server status."""
    from coderfleet.tray import launchagent_installed, _LAUNCHAGENT_LABEL, _LAUNCHAGENT_PLIST
    import subprocess as sp

    # LaunchAgent
    if launchagent_installed():
        click.secho(f"● LaunchAgent installed ({_LAUNCHAGENT_PLIST.name})", fg="green")
    else:
        click.secho("○ LaunchAgent not installed", fg="yellow")

    # Tray process (check via launchctl)
    if sys.platform == "darwin":
        result = sp.run(
            ["launchctl", "list", _LAUNCHAGENT_LABEL],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            click.secho("● Tray running", fg="green")
        else:
            click.secho("○ Tray not running", fg="yellow")

    # Server
    ws = ctx.obj["workspace"]
    pid = sd.read_pid(ws)
    if pid and sd.is_running(pid):
        click.secho(f"● Server running (PID {pid})", fg="green")
    else:
        click.secho("○ Server not running", fg="yellow")
