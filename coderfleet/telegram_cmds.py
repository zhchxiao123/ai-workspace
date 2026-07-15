"""telegram_cmds.py — coderfleet telegram 子命令（与 Web UI 设置页能力对等）。"""
from __future__ import annotations

import asyncio
from pathlib import Path

import click

from coderfleet.server.telegram_bridge import TelegramBridge, TelegramError


@click.group("telegram")
def telegram_group() -> None:
    """Telegram notification channel."""
    pass


@telegram_group.command("test")
@click.pass_context
def cmd_telegram_test(ctx: click.Context) -> None:
    """Send a test message to verify Telegram connectivity.

    \b
    Examples:
      coderfleet telegram test
    """
    ws: Path = ctx.obj["workspace"]
    bridge = TelegramBridge(ws)
    if not bridge.is_configured():
        click.secho("✗ 未配置 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID", fg="red")
        click.secho("  配置方式：coderfleet config set TELEGRAM_BOT_TOKEN <token>", dim=True)
        raise SystemExit(1)
    try:
        asyncio.run(bridge.send_test_message())
    except TelegramError as e:
        click.secho(f"✗ 发送失败：{e}", fg="red")
        click.secho("  国内环境请检查 TELEGRAM_PROXY 是否已配置", dim=True)
        raise SystemExit(1)
    click.secho("✓ 测试消息已发送，请在 Telegram 查收", fg="green")
