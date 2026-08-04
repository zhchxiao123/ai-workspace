"""
task_cmds.py — coderfleet task 子命令

通过 HTTP 调用 CoderFleet 调度服务 API，需先执行 coderfleet server 启动服务。
CODERFLEET_API 环境变量可覆盖服务地址（默认 http://localhost:8765）。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Optional

import click
import httpx


# ── 工具函数 ──────────────────────────────────────────────

def _api_base() -> str:
    return os.environ.get("CODERFLEET_API", "http://localhost:8765").rstrip("/")


def _require_server() -> str:
    """检查服务是否在运行，返回 api base url。失败则 abort。"""
    api = _api_base()
    try:
        httpx.get(f"{api}/api/health", timeout=3).raise_for_status()
    except Exception:
        raise click.ClickException(
            f"CoderFleet 服务未启动，请先执行：coderfleet server\n"
            f"（或设置 CODERFLEET_API=<地址> 指向远端服务）"
        )
    return api


def _http_error_detail(resp: httpx.Response) -> str:
    """从一个非 2xx 响应里抠出 detail 字段；不是 JSON 就原样返回响应体文本。"""
    if "json" in resp.headers.get("content-type", ""):
        return resp.json().get("detail", resp.text)
    return resp.text


def _resolve_project_field(prefer_project: str) -> tuple[str, str]:
    """
    判断 --project 值是路径还是项目名：
    - 绝对路径 / ~ 开头 / ./ 开头 → project 字段（路径匹配）
    - 其他字符串              → project_name 字段（名称匹配）
    返回 (field_name, value)
    """
    expanded = os.path.expanduser(prefer_project)
    if (
        os.path.isabs(expanded)
        or prefer_project.startswith("~")
        or prefer_project.startswith("./")
    ):
        return "project", str(Path(expanded).resolve())
    return "project_name", prefer_project


_STATUS_STYLE = {
    "running":   ("green",  "● 运行中"),
    "pending":   ("yellow", "○ 排队中"),
    "scheduled": ("blue",   "◷ 定时中"),
    "done":      ("cyan",   "✓ 完成  "),
    "failed":    ("red",    "✗ 失败  "),
    "killed":    ("yellow", "⊘ 已终止"),
}


# ── Click 命令组 ──────────────────────────────────────────

@click.group("task")
def task_group() -> None:
    """Task management commands (requires server running)."""
    pass


# ── task run ──────────────────────────────────────────────

@task_group.command("run")
@click.argument("prompt")
@click.option("--project", "prefer_project", default=None, help="项目名称或宿主机路径")
@click.option("--account", default=None, help="指定账号名")
@click.option("--type", "prefer_type", default=None,
              type=click.Choice(["codex", "claude", "opencode"]), help="指定账号类型")
@click.option("--auto", is_flag=True, help="全自动模式（跳过权限确认）")
@click.option("--conversation", "conversation_id", default=None, help="续接已有任务链 ID")
@click.option("--new-chain", "conversation_name", default=None, help="新建任务链并命名")
@click.option("--at", "execute_at", default=None, help="定时执行时间（ISO 8601）")
@click.option("--ephemeral", is_flag=True, help="临时容器模式（docker run --rm，无需持久容器）")
@click.option("--secret", "raw_secrets", multiple=True, metavar="KEY=VAL",
              help="注入临时容器的 secret 环境变量（可多次使用）")
@click.option("--output", "output_dir", default=None, metavar="DIR",
              help="宿主机目录，挂载为容器内 /output（用于收集文件产出）")
def cmd_task_run(
    prompt: str,
    prefer_project: Optional[str],
    account: Optional[str],
    prefer_type: Optional[str],
    auto: bool,
    conversation_id: Optional[str],
    conversation_name: Optional[str],
    execute_at: Optional[str],
    ephemeral: bool,
    raw_secrets: tuple,
    output_dir: Optional[str],
) -> None:
    """Submit a task for execution."""
    api = _require_server()

    body: dict = {"prompt": prompt, "auto": auto}

    if prefer_project:
        field, value = _resolve_project_field(prefer_project)
        body[field] = value
    if account:
        body["account"] = account
    if prefer_type:
        body["type"] = prefer_type
    if conversation_id:
        body["conversation_id"] = conversation_id
    if conversation_name:
        body["conversation_name"] = conversation_name
    if execute_at:
        body["execute_at"] = execute_at
    if ephemeral:
        body["ephemeral"] = True
    if raw_secrets:
        parsed: dict[str, str] = {}
        for s in raw_secrets:
            if "=" not in s:
                raise click.ClickException(f"--secret 格式错误（应为 KEY=VAL）：{s}")
            k, v = s.split("=", 1)
            parsed[k.strip()] = v
        body["secrets"] = parsed
    if output_dir:
        body["output_dir"] = str(Path(output_dir).expanduser().resolve())

    try:
        r = httpx.post(f"{api}/api/tasks", json=body, timeout=10)
    except httpx.RequestError as e:
        raise click.ClickException(f"请求失败：{e}")

    if r.status_code != 201:
        raise click.ClickException(f"提交失败（HTTP {r.status_code}）：{_http_error_detail(r)}")

    d = r.json()
    conv = d.get("conversation_id", "")
    project_label = d["project"].split("/")[-1] if d.get("project") else d.get("project_name", "")

    click.secho(f"✓ 任务已提交：{d['id']}", fg="green")
    mode_label = "  [临时容器]" if d.get("ephemeral") else ""
    click.echo(f"  项目：{project_label}  账号：{d['account']} ({d['type']}){mode_label}")
    if d.get("status") == "scheduled":
        click.echo(f"  定时：{d.get('execute_at')}")
    elif d.get("status") == "pending":
        click.secho(f"  状态：排队中（账号忙碌）", fg="yellow")
    if conv:
        click.echo(f"  任务链：{conv}")
    click.echo(f"  查看日志：coderfleet task logs {d['id']}")
    click.echo(f"  实时跟踪：coderfleet task logs {d['id']} -f")


# ── task list ─────────────────────────────────────────────

@task_group.command("list")
@click.option("--account", default=None, help="按账号名过滤")
@click.option("--status", "status_filter", default=None,
              type=click.Choice(["running", "pending", "scheduled", "done", "failed", "killed"]),
              help="按状态过滤")
@click.option("--limit", default=200, show_default=True, help="最多显示条数")
@click.option("--all", "include_archived", is_flag=True, help="包含已归档的任务")
def cmd_task_list(
    account: Optional[str],
    status_filter: Optional[str],
    limit: int,
    include_archived: bool,
) -> None:
    """List tasks."""
    api = _require_server()

    params: dict = {"limit": limit}
    if account:
        params["account"] = account
    if status_filter:
        params["status"] = status_filter
    if include_archived:
        params["include_archived"] = "true"

    try:
        r = httpx.get(f"{api}/api/tasks", params=params, timeout=10)
    except httpx.RequestError as e:
        raise click.ClickException(f"请求失败：{e}")

    if r.status_code != 200:
        raise click.ClickException(f"获取任务列表失败（HTTP {r.status_code}）")

    tasks = r.json()
    if not tasks:
        click.secho("⚠ 暂无任务记录", fg="yellow")
        return

    click.echo()
    click.echo("  ── 任务列表 " + "─" * 62)
    click.echo(f"  {'任务 ID':<22} {'状态':<10} {'账号':<18} {'类型':<8}  任务描述")
    click.echo("  " + "─" * 80)

    for t in tasks:
        color, label = _STATUS_STYLE.get(t["status"], ("white", t["status"]))
        prompt = t["prompt"]
        if len(prompt) > 38:
            prompt = prompt[:38] + "…"
        status_str = click.style(f"{label:<10}", fg=color)
        click.echo(f"  {t['id']:<22} {status_str}  {t['account']:<18} {t['type']:<8}  {prompt}")

    click.echo()


# ── task status ───────────────────────────────────────────

@task_group.command("status")
@click.argument("task_id")
def cmd_task_status(task_id: str) -> None:
    """Show task details."""
    api = _require_server()

    try:
        r = httpx.get(f"{api}/api/tasks/{task_id}", timeout=10)
    except httpx.RequestError as e:
        raise click.ClickException(f"请求失败：{e}")

    if r.status_code == 404:
        raise click.ClickException(f"任务 '{task_id}' 不存在")
    if r.status_code != 200:
        raise click.ClickException(f"查询失败（HTTP {r.status_code}）")

    d = r.json()
    color, label = _STATUS_STYLE.get(d["status"], ("white", d["status"]))

    click.echo()
    click.echo(f"  ID：      {d['id']}")
    click.echo(f"  状态：    {click.style(label, fg=color)}")
    click.echo(f"  账号：    {d['account']} ({d['type']})")
    click.echo(f"  项目：    {d['project']}")
    if d.get("project_name"):
        click.echo(f"  项目名：  {d['project_name']}")
    if d.get("git_branch"):
        worktree_suffix = " (worktree)" if d.get("git_worktree") else ""
        click.echo(f"  分支：    {d['git_branch']}{worktree_suffix}")
        added, removed = d.get("git_diff_added", 0), d.get("git_diff_removed", 0)
        if added or removed:
            diff_str = click.style(f"+{added}", fg="green") + " " + click.style(f"-{removed}", fg="red")
            click.echo(f"  变更：    {diff_str}")
    click.echo(f"  创建：    {d['created']}")
    click.echo(f"  完成：    {d.get('finished') or '-'}")
    if d.get("execute_at"):
        click.echo(f"  定时：    {d['execute_at']}")
    if d.get("conversation_id"):
        click.echo(f"  任务链：  {d['conversation_id']}")
    if d.get("native_session_id"):
        click.echo(f"  会话ID：  {d['native_session_id']}")
    click.echo(f"  任务描述：{d['prompt']}")
    pending = d.get("pending_intervention")
    if pending:
        click.echo()
        click.secho("  ⏸ 待回答：", fg="yellow")
        for q in pending.get("questions", []):
            click.echo(f"    - {q.get('question', '')}")
        click.echo(f"    （coderfleet task answer {task_id} --json '<回答 JSON>'）")
    click.echo()


# ── task logs ─────────────────────────────────────────────

@task_group.command("logs")
@click.argument("task_id")
@click.option("-f", "--follow", is_flag=True, help="实时跟踪日志（流式输出）")
def cmd_task_logs(task_id: str, follow: bool) -> None:
    """Show task logs. Use -f to stream in real time."""
    api = _require_server()

    if follow:
        click.secho("实时跟踪日志（Ctrl+C 退出）...", dim=True)
        url = f"{api}/api/tasks/{task_id}/logs/stream?tail=80"
        try:
            with httpx.stream("GET", url, timeout=None) as r:
                if r.status_code == 404:
                    raise click.ClickException(f"任务 '{task_id}' 不存在")
                for line in r.iter_lines():
                    if line == "data: [DONE]":
                        break
                    if line.startswith("data: "):
                        click.echo(line[6:])
        except KeyboardInterrupt:
            pass
        except httpx.RequestError as e:
            raise click.ClickException(f"请求失败：{e}")
    else:
        try:
            r = httpx.get(f"{api}/api/tasks/{task_id}/logs", timeout=30)
        except httpx.RequestError as e:
            raise click.ClickException(f"请求失败：{e}")

        if r.status_code == 404:
            raise click.ClickException(f"任务 '{task_id}' 的日志不存在")
        if r.status_code != 200:
            raise click.ClickException(f"获取日志失败（HTTP {r.status_code}）")

        click.echo(r.text, nl=False)


# ── task kill ─────────────────────────────────────────────

@task_group.command("kill")
@click.argument("task_id")
def cmd_task_kill(task_id: str) -> None:
    """Kill a running or pending task."""
    api = _require_server()

    try:
        r = httpx.delete(f"{api}/api/tasks/{task_id}", timeout=10)
    except httpx.RequestError as e:
        raise click.ClickException(f"请求失败：{e}")

    if r.status_code == 200:
        click.secho(f"✓ 已终止任务 {task_id}", fg="green")
    elif r.status_code == 404:
        raise click.ClickException(f"任务 '{task_id}' 不存在")
    else:
        raise click.ClickException(f"终止失败（HTTP {r.status_code}）：{_http_error_detail(r)}")


# ── task answer（Intervention，见 issue #69） ────────────────

@task_group.command("answer")
@click.argument("task_id")
@click.option("--json", "answers_json", required=True,
              help='JSON 格式的回答，形如 \'{"问题原文": "选项label"}\'')
def cmd_task_answer(task_id: str, answers_json: str) -> None:
    """Answer a task's pending Intervention question."""
    api = _require_server()

    try:
        answers = json.loads(answers_json)
    except json.JSONDecodeError as e:
        raise click.ClickException(f"--json 不是合法的 JSON：{e}")

    try:
        r = httpx.get(f"{api}/api/tasks/{task_id}", timeout=10)
    except httpx.RequestError as e:
        raise click.ClickException(f"请求失败：{e}")

    if r.status_code == 404:
        raise click.ClickException(f"任务 '{task_id}' 不存在")
    if r.status_code != 200:
        raise click.ClickException(f"查询失败（HTTP {r.status_code}）")

    pending = r.json().get("pending_intervention")
    if not pending:
        raise click.ClickException(f"任务 '{task_id}' 当前没有待回答的问题")

    try:
        r2 = httpx.post(
            f"{api}/api/tasks/{task_id}/answer", timeout=10,
            json={
                "tool_call_id": pending["tool_call_id"],
                "token": pending["token"],
                "answers": answers,
            },
        )
    except httpx.RequestError as e:
        raise click.ClickException(f"请求失败：{e}")

    if r2.status_code == 200:
        click.secho("✓ 已提交回答", fg="green")
    else:
        raise click.ClickException(f"提交失败（HTTP {r2.status_code}）：{_http_error_detail(r2)}")


# ── task clean ────────────────────────────────────────────

@task_group.command("clean")
@click.argument("keep", default=30, required=False)
def cmd_task_clean(keep: int) -> None:
    """Clean old task records, keeping the N most recent (default: 30)."""
    api = _require_server()

    try:
        r = httpx.post(f"{api}/api/tasks/clean", params={"keep": keep}, timeout=10)
    except httpx.RequestError as e:
        raise click.ClickException(f"请求失败：{e}")

    if r.status_code != 200:
        raise click.ClickException(f"清理失败（HTTP {r.status_code}）")

    d = r.json()
    click.secho(f"✓ 已清理 {d['cleaned']} 条历史记录（保留 {d['kept']} 条）", fg="green")
