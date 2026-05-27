"""
config_cmds.py — coderfleet account / project 子命令
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Optional

import click

from coderfleet.config import ensure_workspace, parse_conf, remove_conf_entry, write_conf_line
from coderfleet.account_type_registry import ACCOUNT_TYPES, env_auth_type_ids

_NAME_RE = re.compile(r"^[a-zA-Z0-9-]+$")


def _container_name(project_name: str, acc_type: str) -> str:
    return f"{acc_type}-{project_name}"


def _is_running(container: str) -> bool:
    r = subprocess.run(
        ["docker", "inspect", container, "--format", "{{.State.Running}}"],
        capture_output=True, text=True,
    )
    return r.stdout.strip() == "true"


# ── account ───────────────────────────────────────────────────


@click.group("account")
def account_group() -> None:
    """Account management commands."""
    pass


@account_group.command("add")
@click.argument("name")
@click.argument("typearg", metavar="TYPE=<type>")
@click.option("--auth", default="login", type=click.Choice(["login", "env"]),
              show_default=True, help="Authentication method")
@click.option("--env-file", "env_file", default=None,
              help="Env file path (for --auth env, default: accounts/<name>/env)")
@click.option("--proxy", default="relay", type=click.Choice(["relay", "off"]),
              show_default=True, help="Proxy mode")
@click.pass_context
def cmd_account_add(
    ctx: click.Context,
    name: str,
    typearg: str,
    auth: str,
    env_file: Optional[str],
    proxy: str,
) -> None:
    """Add a new account. TYPE can be any registered type (run 'account list-types' to see all)."""
    ws: Path = ctx.obj["workspace"]

    if not _NAME_RE.match(name):
        raise click.ClickException("NAME 只能包含字母、数字、连字符")

    valid_types = list(ACCOUNT_TYPES.keys())
    acc_type = typearg[5:] if typearg.startswith("TYPE=") else typearg
    if acc_type not in valid_types:
        raise click.ClickException(
            f"TYPE 不合法，只支持：{' / '.join(valid_types)}"
        )

    env_auth_types = env_auth_type_ids()
    if auth == "env":
        if acc_type not in env_auth_types:
            raise click.ClickException(
                f"AUTH=env 不支持 TYPE={acc_type}，"
                f"目前支持：{' / '.join(env_auth_types)}"
            )
        if not env_file:
            env_file = f"./accounts/{name}/env"

    accounts_conf = ws / "accounts.conf"
    for r in parse_conf(accounts_conf):
        if r.get("NAME") == name:
            raise click.ClickException(f"账号 '{name}' 已存在，若要修改请先 remove 再 add")

    tokens: dict[str, str] = {"NAME": name, "TYPE": acc_type, "AUTH": auth}
    if auth == "env" and env_file:
        tokens["ENV_FILE"] = env_file
    tokens["PROXY"] = proxy

    ensure_workspace(ws)
    write_conf_line(accounts_conf, tokens)
    (ws / "accounts" / name).mkdir(parents=True, exist_ok=True)

    click.secho(f"✓ 账号 '{name}' 已添加（类型：{acc_type}，认证：{auth}，代理：{proxy}）", fg="green")
    if auth == "env":
        click.secho(f"  ENV 文件路径：{env_file}", fg="yellow")
        spec = ACCOUNT_TYPES[acc_type]
        if spec.env_hint:
            lines = spec.env_hint.splitlines()
            click.secho(f"  {lines[0]}", fg="yellow")
            for line in lines[1:]:
                click.secho(f"  {line}", dim=True)
    click.secho(f"  接下来：coderfleet project add <项目名> {name} <项目路径>", dim=True)
    click.secho("  执行 coderfleet apply 使配置生效", fg="yellow")


@account_group.command("remove")
@click.argument("name")
@click.pass_context
def cmd_account_remove(ctx: click.Context, name: str) -> None:
    """Remove an account (stops associated containers)."""
    ws: Path = ctx.obj["workspace"]
    accounts_conf = ws / "accounts.conf"
    projects_conf = ws / "projects.conf"

    accounts = {r["NAME"]: r for r in parse_conf(accounts_conf) if "NAME" in r}
    if name not in accounts:
        raise click.ClickException(f"账号 '{name}' 不存在")

    acc_type = accounts[name].get("TYPE", "")
    if acc_type:
        for p in parse_conf(projects_conf):
            if p.get("ACCOUNT") != name:
                continue
            pname = p.get("NAME", "")
            ctr = _container_name(pname, acc_type)
            if _is_running(ctr):
                click.echo(f"  停止容器 {ctr}...")
                subprocess.run(["docker", "stop", ctr], capture_output=True)
                subprocess.run(["docker", "rm", ctr], capture_output=True)

    remove_conf_entry(accounts_conf, "NAME", name)
    click.secho(f"✓ 账号 '{name}' 已从配置中移除", fg="green")
    click.secho(f"  认证数据保留在 accounts/{name}/  如需清除：rm -rf accounts/{name}", fg="yellow")
    click.secho("  执行 coderfleet apply 重新生成配置", fg="yellow")


@account_group.command("list")
@click.pass_context
def cmd_account_list(ctx: click.Context) -> None:
    """List all accounts."""
    ws: Path = ctx.obj["workspace"]
    accounts = parse_conf(ws / "accounts.conf")
    projects = parse_conf(ws / "projects.conf")

    click.echo()
    click.echo("  ── 账号列表 " + "─" * 58)
    click.echo(f"  {'名称':<20} {'类型':<8} {'认证':<8} {'代理':<8}  状态")
    click.echo("  " + "─" * 70)

    if not accounts:
        click.secho("  暂无账号，使用 coderfleet account add 添加", fg="yellow")
        click.echo()
        return

    for rec in accounts:
        name = rec.get("NAME", "")
        acc_type = rec.get("TYPE", "")
        auth = rec.get("AUTH", "login")
        proxy = rec.get("PROXY", "relay")

        any_running = any(
            _is_running(_container_name(p.get("NAME", ""), acc_type))
            for p in projects
            if p.get("ACCOUNT") == name
        )
        status = click.style("● 运行中", fg="green") if any_running else click.style("○ 已停止", fg="yellow")
        click.echo(f"  {name:<20} {acc_type:<8} {auth:<8} {proxy:<8}  {status}")

    click.echo()


# ── project ───────────────────────────────────────────────────


@click.group("project")
def project_group() -> None:
    """Project management commands."""
    pass


@project_group.command("add")
@click.argument("name")
@click.argument("account")
@click.argument("path")
@click.pass_context
def cmd_project_add(
    ctx: click.Context,
    name: str,
    account: str,
    path: str,
) -> None:
    """Add a new project."""
    ws: Path = ctx.obj["workspace"]

    if not _NAME_RE.match(name):
        raise click.ClickException("项目名称只能包含字母、数字、连字符")

    accounts_conf = ws / "accounts.conf"
    accounts = {r["NAME"] for r in parse_conf(accounts_conf) if "NAME" in r}
    if account not in accounts:
        raise click.ClickException(f"账号 '{account}' 不存在")

    projects_conf = ws / "projects.conf"
    for p in parse_conf(projects_conf):
        if p.get("NAME") == name:
            raise click.ClickException(f"项目 '{name}' 已存在，若要修改请先 remove 再 add")

    ensure_workspace(ws)
    write_conf_line(projects_conf, {"NAME": name, "ACCOUNT": account, "PATH": path})

    expanded = str(Path(path).expanduser())
    click.secho(f"✓ 项目 '{name}' 已添加（账号：{account}  路径：{expanded}）", fg="green")
    click.secho("  执行 coderfleet apply 使配置生效", fg="yellow")


@project_group.command("remove")
@click.argument("name")
@click.pass_context
def cmd_project_remove(ctx: click.Context, name: str) -> None:
    """Remove a project."""
    ws: Path = ctx.obj["workspace"]
    projects_conf = ws / "projects.conf"

    if not any(p.get("NAME") == name for p in parse_conf(projects_conf)):
        raise click.ClickException(f"项目 '{name}' 不存在")

    remove_conf_entry(projects_conf, "NAME", name)
    click.secho(f"✓ 项目 '{name}' 已移除", fg="green")
    click.secho("  执行 coderfleet apply 重新生成配置", fg="yellow")


@project_group.command("list")
@click.pass_context
def cmd_project_list(ctx: click.Context) -> None:
    """List all projects."""
    ws: Path = ctx.obj["workspace"]
    projects = parse_conf(ws / "projects.conf")

    click.echo()
    click.echo("  ── 项目列表 " + "─" * 62)
    click.echo(f"  {'名称':<20} {'账号':<20}  路径")
    click.echo("  " + "─" * 74)

    if not projects:
        click.secho("  暂无项目，使用 coderfleet project add 添加", fg="yellow")
        click.echo()
        return

    for p in projects:
        name = p.get("NAME", "")
        account = p.get("ACCOUNT", "")
        path = p.get("PATH", "")
        click.echo(f"  {name:<20} {account:<20}  {path}")

    click.echo()
