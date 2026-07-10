"""
workflow_cmds.py — coderfleet workflow 子命令

为「工作流」提供与 Web UI 对等的 CLI 能力（实体统一到 WorkflowRun，见 #17）。
通过 HTTP 调用 CoderFleet 调度服务 API，需先执行 coderfleet server 启动服务。
CODERFLEET_API 环境变量可覆盖服务地址（默认 http://localhost:8765）。
"""
from __future__ import annotations

from typing import Optional

import click
import httpx

from coderfleet.task_cmds import _require_server


_NODE_STATE_LABEL = {
    "pending": "待执行",
    "waiting_deps": "等待依赖",
    "running": "运行中",
    "succeeded": "成功",
    "failed": "失败",
    "skipped": "已跳过",
    "cancelled": "已取消",
    "waiting_approval": "待批准",
}


def _detail(r: httpx.Response) -> str:
    if r.headers.get("content-type", "").startswith("application/json"):
        try:
            return r.json().get("detail", r.text)
        except Exception:
            return r.text
    return r.text


# ── workflow 命令组 ───────────────────────────────────────

@click.group("workflow")
def workflow_group() -> None:
    """Workflow commands (requires server running)."""
    pass


@workflow_group.group("template")
def template_group() -> None:
    """Workflow-template commands."""
    pass


@template_group.command("list")
def cmd_template_list() -> None:
    """List workflow templates."""
    api = _require_server()
    try:
        r = httpx.get(f"{api}/api/workflow-templates", timeout=10)
        r.raise_for_status()
    except httpx.HTTPError as e:
        raise click.ClickException(f"请求失败：{e}")
    templates = r.json()
    if not templates:
        click.echo("暂无工作流模板。")
        return
    for t in templates:
        nodes = t.get("nodes") or []
        click.echo(f"{click.style(t['id'], fg='cyan')}  {t.get('name','')}  · {len(nodes)} 个节点")
        if t.get("description"):
            click.echo(f"       {t['description']}")


@template_group.command("run")
@click.argument("template_id")
@click.option("--input", "input_str", default="", help="工作流输入（替换节点 prompt 中的 {{input}}）")
@click.option("--project", "project_pairs", multiple=True, metavar="ROLE=NAME",
              help="运行时角色→项目映射（可多次）")
@click.option("--default-project", default="", help="运行时默认项目")
@click.option("--default-account", default="", help="临时沙箱默认账号")
@click.option("--workspace-policy", default="isolated",
              type=click.Choice(["isolated", "shared_ephemeral"]),
              show_default=True)
def cmd_template_run(
    template_id: str,
    input_str: str,
    project_pairs: tuple,
    default_project: str,
    default_account: str,
    workspace_policy: str,
) -> None:
    """Run a workflow TEMPLATE_ID."""
    api = _require_server()
    project_map: dict[str, str] = {}
    for pair in project_pairs:
        if "=" not in pair:
            raise click.ClickException(f"--project 格式错误（应为 ROLE=NAME）：{pair}")
        k, v = pair.split("=", 1)
        project_map[k.strip()] = v.strip()
    body = {
        "input": input_str,
        "project_map": project_map,
        "default_project": default_project,
        "default_account": default_account,
        "workspace_policy": workspace_policy,
    }
    try:
        r = httpx.post(f"{api}/api/workflow-templates/{template_id}/run", json=body, timeout=30)
    except httpx.RequestError as e:
        raise click.ClickException(f"请求失败：{e}")
    if r.status_code != 201:
        raise click.ClickException(f"运行失败（HTTP {r.status_code}）：{_detail(r)}")
    d = r.json()
    click.secho(f"✓ 已触发工作流运行：{d['id']}", fg="green")
    click.echo(f"  状态：{d.get('status','')}  查看：coderfleet workflow run logs {d['id']}")


@workflow_group.group("run")
def run_group() -> None:
    """Workflow-run commands."""
    pass


@run_group.command("list")
def cmd_run_list() -> None:
    """List workflow runs."""
    api = _require_server()
    try:
        r = httpx.get(f"{api}/api/workflow-runs", timeout=10)
        r.raise_for_status()
    except httpx.HTTPError as e:
        raise click.ClickException(f"请求失败：{e}")
    runs = r.json()
    if not runs:
        click.echo("暂无工作流运行。")
        return
    for run in runs:
        nodes = run.get("node_executions") or []
        done = sum(1 for n in nodes if n.get("state") == "succeeded")
        click.echo(
            f"{click.style(run['id'], fg='cyan')}  {run.get('name','')}  "
            f"[{run.get('status','')}]  节点 {done}/{len(nodes)}"
        )


@run_group.command("logs")
@click.argument("run_id")
def cmd_run_logs(run_id: str) -> None:
    """Show node-by-node state/output for RUN_ID."""
    api = _require_server()
    try:
        r = httpx.get(f"{api}/api/workflow-runs/{run_id}", timeout=10)
    except httpx.RequestError as e:
        raise click.ClickException(f"请求失败：{e}")
    if r.status_code == 404:
        raise click.ClickException(f"工作流运行 '{run_id}' 不存在")
    r.raise_for_status()
    run = r.json()
    click.secho(f"工作流运行 {run['id']}  [{run.get('status','')}]", fg="cyan", bold=True)
    for n in run.get("node_executions") or []:
        label = _NODE_STATE_LABEL.get(n.get("state", ""), n.get("state", ""))
        click.echo(f"  · {n.get('name') or n.get('node_id')}  [{label}]"
                   + (f"  task={n['task_id']}" if n.get("task_id") else ""))
        if n.get("error_message"):
            click.echo(f"      错误：{n['error_message']}")
        out = (n.get("outputs") or {}).get("text", "")
        if out:
            snippet = out.strip().splitlines()[0][:100] if out.strip() else ""
            if snippet:
                click.echo(f"      输出：{snippet}")


@run_group.command("approve")
@click.argument("run_id")
def cmd_run_approve(run_id: str) -> None:
    """Approve a waiting_approval RUN_ID so it continues."""
    api = _require_server()
    try:
        gr = httpx.get(f"{api}/api/workflow-runs/{run_id}", timeout=10)
    except httpx.RequestError as e:
        raise click.ClickException(f"请求失败：{e}")
    if gr.status_code == 404:
        raise click.ClickException(f"工作流运行 '{run_id}' 不存在")
    gr.raise_for_status()
    token = gr.json().get("approval_token", "")
    try:
        r = httpx.post(
            f"{api}/api/workflow-runs/{run_id}/approve",
            params={"token": token}, timeout=15,
        )
    except httpx.RequestError as e:
        raise click.ClickException(f"请求失败：{e}")
    if r.status_code >= 400:
        raise click.ClickException(f"批准失败（HTTP {r.status_code}）：{_detail(r)}")
    click.secho("✓ 已批准，工作流继续执行。", fg="green")
