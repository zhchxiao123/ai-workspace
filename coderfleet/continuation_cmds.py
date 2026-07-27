"""Conversation Continuation CLI commands."""
from __future__ import annotations

import click
import httpx

from coderfleet.task_cmds import _http_error_detail, _require_server


@click.group("continuation")
def continuation_group() -> None:
    """Manage delayed Conversation follow-ups."""


@continuation_group.command("list")
@click.option("--conversation", "conversation_id", default=None)
def list_continuations(conversation_id: str | None) -> None:
    api = _require_server()
    params = {"conversation_id": conversation_id} if conversation_id else {}
    response = httpx.get(f"{api}/api/continuations", params=params, timeout=10)
    if response.status_code >= 400:
        raise click.ClickException(_http_error_detail(response))
    records = response.json()
    if not records:
        click.echo("暂无后续动作。")
        return
    for record in records:
        click.echo(
            f"{record['id']}  {record['status']}  "
            f"conversation={record['conversation_id']}  "
            f"task={record.get('result_task_id') or '-'}"
        )
        click.echo(f"  {record['prompt']}")


@continuation_group.command("add")
@click.argument("source_task_id")
@click.argument("prompt")
@click.option("--after", "delay_seconds", type=int, required=True, help="Delay in seconds (10..2592000).")
@click.option("--expires", "expires_in_seconds", type=int, default=86400, show_default=True)
def add_continuation(
    source_task_id: str, prompt: str, delay_seconds: int, expires_in_seconds: int,
) -> None:
    api = _require_server()
    response = httpx.post(
        f"{api}/api/continuations",
        json={
            "source_task_id": source_task_id,
            "prompt": prompt,
            "triggers": [{"type": "timer", "delay_seconds": delay_seconds}],
            "expires_in_seconds": expires_in_seconds,
        },
        timeout=10,
    )
    if response.status_code >= 400:
        raise click.ClickException(_http_error_detail(response))
    record = response.json()
    click.secho(f"✓ 已安排后续动作：{record['id']}", fg="green")
    click.echo(f"  会话：{record['conversation_id']}")
    click.echo(f"  触发：{record['triggers'][0].get('fire_at')}")


@continuation_group.command("cancel")
@click.argument("continuation_id")
def cancel_continuation(continuation_id: str) -> None:
    api = _require_server()
    response = httpx.delete(f"{api}/api/continuations/{continuation_id}", timeout=10)
    if response.status_code >= 400:
        raise click.ClickException(_http_error_detail(response))
    click.secho(f"✓ 已取消后续动作 {continuation_id}", fg="green")


@continuation_group.command("trigger")
@click.argument("continuation_id")
def trigger_continuation(continuation_id: str) -> None:
    api = _require_server()
    response = httpx.post(f"{api}/api/continuations/{continuation_id}/trigger", timeout=10)
    if response.status_code >= 400:
        raise click.ClickException(_http_error_detail(response))
    record = response.json()
    click.secho(f"✓ 已触发后续动作 {continuation_id}", fg="green")
    click.echo(f"  任务：{record.get('result_task_id') or '-'}")
