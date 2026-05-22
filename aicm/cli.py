from __future__ import annotations

import os
import click
from pathlib import Path

from aicm.config import get_workspace, load_config


@click.group()
@click.pass_context
def main(ctx: click.Context) -> None:
    """AICM - AI Code Manager

    Manages multiple Claude Code / Codex accounts in isolated Docker containers.

    Workspace location: AICM_WORKSPACE env var, or ~/.aicm/ by default.
    """
    ctx.ensure_object(dict)
    ctx.obj["workspace"] = get_workspace()


@main.command("init")
@click.pass_context
def cmd_init(ctx: click.Context) -> None:
    """Initialize the AICM workspace (interactive setup wizard)."""
    from aicm.init_wizard import run_init_wizard
    run_init_wizard(ctx.obj["workspace"])


@main.command("server")
@click.option("--port", default=None, type=int, envvar="AICM_PORT", help="Port to listen on (default: 8765)")
@click.pass_context
def cmd_server(ctx: click.Context, port: int | None) -> None:
    """Start the FastAPI scheduler server and Web UI."""
    import uvicorn

    ws = ctx.obj["workspace"]
    cfg = load_config(ws)
    resolved_port = port or int(cfg.get("AICM_PORT", 8765))

    os.environ["AICM_WORKSPACE"] = str(ws)
    os.environ["AICM_PORT"] = str(resolved_port)

    click.echo(f"Workspace: {ws}")
    click.echo(f"Starting AICM server at http://localhost:{resolved_port}")

    uvicorn.run(
        "aicm.server.main:app",
        host="0.0.0.0",
        port=resolved_port,
        reload=False,
        workers=1,
    )
