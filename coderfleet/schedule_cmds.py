"""
schedule_cmds.py — coderfleet schedule / digest 子命令

为「定时计划」与「任务日报」提供与 Web UI 对等的 CLI 能力。
通过 HTTP 调用 CoderFleet 调度服务 API，需先执行 coderfleet server 启动服务。
CODERFLEET_API 环境变量可覆盖服务地址（默认 http://localhost:8765）。
"""
from __future__ import annotations

from typing import Optional

import click
import httpx

from coderfleet.task_cmds import _api_base, _require_server


# ── 展示辅助 ──────────────────────────────────────────────

_WEEKDAYS = ["一", "二", "三", "四", "五", "六", "日"]


def _fmt_rule(s: dict) -> str:
    """把一条定时计划的调度规则渲染成人类可读文本。"""
    stype = s.get("schedule_type", "")
    if stype == "daily":
        return f"每天 {s.get('time_of_day') or '??:??'}"
    if stype == "weekly":
        days = "".join(_WEEKDAYS[d] for d in s.get("days_of_week", []) if 0 <= d < 7)
        return f"每周{days or '?'} {s.get('time_of_day') or '??:??'}"
    if stype == "hourly":
        m = s.get("minute_of_hour")
        return f"每小时 :{m:02d}" if isinstance(m, int) else "每小时"
    if stype == "cron":
        return f"cron[{s.get('cron_expr') or '?'}]"
    return stype or "-"


def _fmt_target(s: dict) -> str:
    if s.get("target_type") == "workflow":
        return f"工作流<{s.get('template_id') or '?'}>"
    return "任务"


# ── schedule 命令组 ───────────────────────────────────────

@click.group("schedule")
def schedule_group() -> None:
    """Scheduled-plan commands (requires server running)."""
    pass


@schedule_group.command("list")
def cmd_schedule_list() -> None:
    """List all scheduled plans."""
    api = _require_server()
    try:
        r = httpx.get(f"{api}/api/schedules", timeout=10)
        r.raise_for_status()
    except httpx.HTTPError as e:
        raise click.ClickException(f"请求失败：{e}")

    schedules = r.json()
    if not schedules:
        click.echo("暂无定时计划。")
        return

    for s in schedules:
        flag = click.style("● 启用", fg="green") if s.get("enabled") else click.style("○ 停用", fg="yellow")
        click.echo(
            f"{flag}  {click.style(s['id'], fg='cyan')}  {s.get('name', '')}"
        )
        click.echo(
            f"       规则：{_fmt_rule(s)}   目标：{_fmt_target(s)}   项目：{s.get('project_name') or '-'}"
        )
        nxt = s.get("next_run_at") or "-"
        last = s.get("last_run_at") or "从未"
        click.echo(f"       下次：{nxt}   上次：{last}")


@schedule_group.command("run")
@click.argument("sched_id")
def cmd_schedule_run(sched_id: str) -> None:
    """Trigger a scheduled plan to run once, immediately."""
    api = _require_server()
    try:
        r = httpx.post(f"{api}/api/schedules/{sched_id}/run-now", timeout=30)
    except httpx.RequestError as e:
        raise click.ClickException(f"请求失败：{e}")

    if r.status_code == 404:
        raise click.ClickException(f"定时计划 '{sched_id}' 不存在")
    if r.status_code != 201:
        detail = r.json().get("detail", r.text) if r.headers.get("content-type", "").startswith("application/json") else r.text
        raise click.ClickException(f"触发失败（HTTP {r.status_code}）：{detail}")

    d = r.json()
    run_type = d.get("run_type", "task")
    if run_type == "workflow" and d.get("workflow_run"):
        wr = d["workflow_run"]
        click.secho(f"✓ 已触发工作流运行：{wr['id']}", fg="green")
        click.echo(f"  查看：coderfleet workflow run logs {wr['id']}")
    elif d.get("task"):
        t = d["task"]
        click.secho(f"✓ 已触发任务：{t['id']}", fg="green")
        click.echo(f"  查看日志：coderfleet task logs {t['id']}")
    else:
        click.secho("✓ 已触发。", fg="green")


# ── digest 命令 ───────────────────────────────────────────

@click.command("digest")
@click.argument("date")
@click.option("--generate", is_flag=True, help="触发 AI 摘要生成（异步，稍后再查看）")
def cmd_digest(date: str, generate: bool) -> None:
    """Show the daily task digest for DATE (YYYY-MM-DD)."""
    api = _require_server()

    if generate:
        try:
            gr = httpx.post(f"{api}/api/digest/{date}/generate", timeout=15)
        except httpx.RequestError as e:
            raise click.ClickException(f"请求失败：{e}")
        if gr.status_code == 400:
            raise click.ClickException("日期格式必须为 YYYY-MM-DD")
        if gr.status_code >= 400:
            detail = gr.json().get("detail", gr.text) if gr.headers.get("content-type", "").startswith("application/json") else gr.text
            raise click.ClickException(f"生成失败（HTTP {gr.status_code}）：{detail}")
        click.secho("✓ 已提交 AI 摘要生成任务，稍后重新运行本命令查看结果。", fg="green")

    try:
        r = httpx.get(f"{api}/api/digest/{date}", timeout=10)
    except httpx.RequestError as e:
        raise click.ClickException(f"请求失败：{e}")
    if r.status_code == 400:
        raise click.ClickException("日期格式必须为 YYYY-MM-DD")
    r.raise_for_status()

    d = r.json()
    total = d.get("total_done", 0) + d.get("total_failed", 0) + d.get("total_killed", 0)
    if total == 0:
        click.echo(f"{date} 无任务活动。")
        return

    click.secho(f"📅 {date} 任务日报", fg="cyan", bold=True)
    click.echo(
        f"  完成 {d.get('total_done', 0)}   失败 {d.get('total_failed', 0)}   "
        f"终止 {d.get('total_killed', 0)}"
    )
    click.echo(
        f"  Token：输入 {d.get('tokens_input', 0)} / 输出 {d.get('tokens_output', 0)}   "
        f"成本：${d.get('cost_usd', 0.0):.4f}"
    )

    projects = d.get("projects", [])
    if projects:
        click.echo("  按项目：")
        for p in projects:
            click.echo(
                f"    - {p.get('project_name') or '-'}："
                f"完成 {p.get('done', 0)}，失败 {p.get('failed', 0)}，终止 {p.get('killed', 0)}"
            )

    summary = d.get("ai_summary") or ""
    status = d.get("status", "pending")
    click.echo()
    if summary:
        click.secho("  AI 摘要：", fg="green")
        for line in summary.splitlines():
            click.echo(f"    {line}")
    elif status == "generating":
        click.echo("  AI 摘要：生成中，稍后重新运行本命令查看。")
    else:
        click.echo("  AI 摘要：尚未生成（加 --generate 触发生成）。")
