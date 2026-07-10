"""
board_cmds.py — coderfleet board / card 子命令

为「开发看板」提供与 Web UI 对等的 CLI 能力（看板已降级为单一引用覆盖层，见 #16）。
通过 HTTP 调用 CoderFleet 调度服务 API，需先执行 coderfleet server 启动服务。
CODERFLEET_API 环境变量可覆盖服务地址（默认 http://localhost:8765）。
"""
from __future__ import annotations

from typing import Optional

import click
import httpx

from coderfleet.task_cmds import _require_server


# 看板列（与 BoardCardStatus / 前端 BOARD_COLUMNS 对齐）
_COLUMNS = [
    ("planned", "待规划"),
    ("todo", "待执行"),
    ("running", "执行中"),
    ("review", "待验收"),
    ("done", "已完成"),
    ("blocked", "受阻"),
    ("parked", "已搁置"),
]
_STATUS_CHOICES = [c[0] for c in _COLUMNS]
_STATUS_LABEL = dict(_COLUMNS)


def _get_json(api: str, path: str):
    r = httpx.get(f"{api}{path}", timeout=10)
    r.raise_for_status()
    return r.json()


# ── board 命令组 ──────────────────────────────────────────

@click.group("board")
def board_group() -> None:
    """Development-board commands (requires server running)."""
    pass


@board_group.command("list")
def cmd_board_list() -> None:
    """List boards and a per-column overview of their cards."""
    api = _require_server()
    try:
        boards = _get_json(api, "/api/boards")
    except httpx.HTTPError as e:
        raise click.ClickException(f"请求失败：{e}")

    if not boards:
        click.echo("暂无看板。")
        return

    for b in boards:
        click.echo(f"{click.style(b['id'], fg='cyan')}  {b.get('name', '')}")
        try:
            cards = _get_json(api, f"/api/boards/{b['id']}/cards")
        except httpx.HTTPError:
            cards = []
        by_status: dict[str, list] = {s: [] for s, _ in _COLUMNS}
        for c in cards:
            by_status.setdefault(c.get("status", "planned"), []).append(c)
        counts = "  ".join(
            f"{label} {len(by_status.get(s, []))}" for s, label in _COLUMNS
        )
        click.echo(f"       {counts}")
        for c in cards:
            tasks = c.get("task_ids") or []
            click.echo(
                f"         · [{_STATUS_LABEL.get(c.get('status',''), c.get('status',''))}] "
                f"{click.style(c['id'], fg='cyan')} {c.get('title','')}"
                f"（{len(tasks)} 个任务）"
            )


# ── card 命令组 ───────────────────────────────────────────

@click.group("card")
def card_group() -> None:
    """Board-card commands (requires server running)."""
    pass


@card_group.command("add")
@click.argument("board_id")
@click.argument("title")
@click.option("--project", "project_name", default=None, help="关联项目名")
@click.option("--desc", "description", default="", help="卡片描述")
@click.option("--status", "status", default="planned",
              type=click.Choice(_STATUS_CHOICES), show_default=True, help="初始状态")
def cmd_card_add(
    board_id: str,
    title: str,
    project_name: Optional[str],
    description: str,
    status: str,
) -> None:
    """Add a card to BOARD_ID."""
    api = _require_server()
    body: dict = {"title": title, "description": description, "status": status}
    if project_name:
        body["project_name"] = project_name
    try:
        r = httpx.post(f"{api}/api/boards/{board_id}/cards", json=body, timeout=10)
    except httpx.RequestError as e:
        raise click.ClickException(f"请求失败：{e}")
    if r.status_code == 404:
        detail = r.json().get("detail", r.text) if r.headers.get("content-type", "").startswith("application/json") else r.text
        raise click.ClickException(detail)
    if r.status_code != 201:
        detail = r.json().get("detail", r.text) if r.headers.get("content-type", "").startswith("application/json") else r.text
        raise click.ClickException(f"创建失败（HTTP {r.status_code}）：{detail}")
    d = r.json()
    click.secho(f"✓ 已创建专题：{d['id']}", fg="green")
    click.echo(f"  {d.get('title','')}  状态：{_STATUS_LABEL.get(d.get('status',''), d.get('status',''))}")


@card_group.command("move")
@click.argument("card_id")
@click.argument("status", type=click.Choice(_STATUS_CHOICES))
def cmd_card_move(card_id: str, status: str) -> None:
    """Move CARD_ID to STATUS."""
    api = _require_server()
    try:
        r = httpx.patch(f"{api}/api/board-cards/{card_id}", json={"status": status}, timeout=10)
    except httpx.RequestError as e:
        raise click.ClickException(f"请求失败：{e}")
    if r.status_code == 404:
        raise click.ClickException(f"看板卡片 '{card_id}' 不存在")
    if r.status_code >= 400:
        detail = r.json().get("detail", r.text) if r.headers.get("content-type", "").startswith("application/json") else r.text
        raise click.ClickException(f"移动失败（HTTP {r.status_code}）：{detail}")
    click.secho(f"✓ 已移动到「{_STATUS_LABEL.get(status, status)}」", fg="green")
