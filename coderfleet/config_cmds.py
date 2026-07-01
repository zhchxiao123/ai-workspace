"""
config_cmds.py — coderfleet account / project 子命令
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Optional

import click

from coderfleet.config import ensure_workspace, load_config, parse_conf, remove_conf_entry, set_config, update_conf_field, write_conf_line
from coderfleet.account_type_registry import ACCOUNT_TYPES, env_auth_type_ids
from coderfleet.ports import allocate_ide_port

_NAME_RE = re.compile(r"^[a-zA-Z0-9-]+$")


def _container_name(project_name: str, acc_type: str) -> str:
    return f"{acc_type}-{project_name}"


def _existing_ide_ports(projects: list[dict[str, str]], current_name: str) -> list[int]:
    used: set[int] = set()
    for project in projects:
        if project.get("NAME") == current_name:
            continue
        try:
            used.add(int(project.get("IDE_PORT", "")))
        except ValueError:
            pass
    return list(used)


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
@click.option("--ide", is_flag=True, help="Enable browser IDE (code-server) for this project")
@click.option("--ide-port", type=int, default=None, help="Host port for browser IDE, auto-assigned when omitted")
@click.option("--disabled", is_flag=True, help="Add project in disabled state (no container on apply)")
@click.option("--image", default=None, metavar="IMAGE", help="自定义 Docker 镜像（如 my-image:latest），留空使用共享镜像")
@click.option("--docker-socket", default=None, metavar="SOCKET",
              help="项目级 Docker socket 覆盖：off / auto / /path/to/docker.sock")
@click.pass_context
def cmd_project_add(
    ctx: click.Context,
    name: str,
    account: str,
    path: str,
    ide: bool,
    ide_port: int | None,
    disabled: bool,
    image: Optional[str],
    docker_socket: Optional[str],
) -> None:
    """Add a new project."""
    ws: Path = ctx.obj["workspace"]

    if not _NAME_RE.match(name):
        raise click.ClickException("项目名称只能包含字母、数字、连字符")

    accounts_conf = ws / "accounts.conf"
    accounts = {r["NAME"] for r in parse_conf(accounts_conf) if "NAME" in r}
    if account not in accounts:
        raise click.ClickException(f"账号 '{account}' 不存在")
    if ide and ide_port is not None:
        if ide_port < 1024 or ide_port > 65535:
            raise click.ClickException("IDE 端口必须在 1024-65535 之间")

    projects_conf = ws / "projects.conf"
    existing_projects = parse_conf(projects_conf)
    for p in existing_projects:
        if p.get("NAME") == name:
            raise click.ClickException(f"项目 '{name}' 已存在，若要修改请先 remove 再 add")

    ensure_workspace(ws)
    tokens = {"NAME": name, "ACCOUNT": account, "PATH": path}
    if ide:
        if ide_port is None:
            try:
                ide_port = allocate_ide_port(_existing_ide_ports(existing_projects, name))
            except RuntimeError as e:
                raise click.ClickException(str(e))
        tokens["IDE"] = "on"
        tokens["IDE_PORT"] = str(ide_port)
    if disabled:
        tokens["ACTIVE"] = "off"
    if image:
        tokens["IMAGE"] = image
    if docker_socket:
        tokens["DOCKER_SOCKET"] = docker_socket
    write_conf_line(projects_conf, tokens)

    expanded = str(Path(path).expanduser())
    state_label = "（已禁用）" if disabled else ""
    click.secho(f"✓ 项目 '{name}' 已添加{state_label}（账号：{account}  路径：{expanded}）", fg="green")
    if image:
        click.secho(f"  镜像：{image}", dim=True)
    if docker_socket:
        click.secho(f"  Docker socket：{docker_socket}", dim=True)
    click.secho("  执行 coderfleet apply 使配置生效", fg="yellow")


@project_group.command("set-image")
@click.argument("name")
@click.argument("image")
@click.pass_context
def cmd_project_set_image(ctx: click.Context, name: str, image: str) -> None:
    """Set or clear a project's custom Docker image.

    \b
    Examples:
      coderfleet project set-image my-project my-image:latest  # 设置专属镜像
      coderfleet project set-image my-project -               # 清除，恢复共享镜像
    """
    ws: Path = ctx.obj["workspace"]
    projects_conf = ws / "projects.conf"

    if not any(p.get("NAME") == name for p in parse_conf(projects_conf)):
        raise click.ClickException(f"项目 '{name}' 不存在")

    if image == "-":
        from coderfleet.config import remove_conf_entry, update_conf_field
        # 把 IMAGE= 这个 token 从该行移除（update_conf_field 找不到就不改）
        # 用更直接的方式：先读行，去掉 IMAGE=xxx token，再写回
        import re
        lines = projects_conf.read_text(encoding="utf-8").splitlines(keepends=True)
        new_lines = []
        name_pat = re.compile(rf"\bNAME={re.escape(name)}(\s|$)")
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                new_lines.append(line)
                continue
            if name_pat.search(line):
                tokens = [t for t in line.rstrip("\n").split() if not t.upper().startswith("IMAGE=")]
                new_lines.append("  ".join(tokens) + "\n")
            else:
                new_lines.append(line)
        projects_conf.write_text("".join(new_lines), encoding="utf-8")
        click.secho(f"✓ 项目 '{name}' 已恢复使用共享镜像", fg="green")
    else:
        update_conf_field(projects_conf, name, "IMAGE", image)
        click.secho(f"✓ 项目 '{name}' 已设置镜像：{image}", fg="green")

    click.secho("  执行 coderfleet apply 使配置生效", fg="yellow")


@project_group.command("set-docker-socket")
@click.argument("name")
@click.argument("socket")
@click.pass_context
def cmd_project_set_docker_socket(ctx: click.Context, name: str, socket: str) -> None:
    """Set, disable, or clear a project's Docker socket override.

    \b
    Examples:
      coderfleet project set-docker-socket my-project auto
      coderfleet project set-docker-socket my-project off
      coderfleet project set-docker-socket my-project -     # 清除，继承全局配置
    """
    ws: Path = ctx.obj["workspace"]
    projects_conf = ws / "projects.conf"

    if not any(p.get("NAME") == name for p in parse_conf(projects_conf)):
        raise click.ClickException(f"项目 '{name}' 不存在")

    if socket == "-":
        lines = projects_conf.read_text(encoding="utf-8").splitlines(keepends=True)
        new_lines = []
        for line in lines:
            if re.search(rf"\bNAME={re.escape(name)}(\s|$)", line):
                tokens = [t for t in line.rstrip("\n").split() if not t.upper().startswith("DOCKER_SOCKET=")]
                new_lines.append("  ".join(tokens) + "\n")
            else:
                new_lines.append(line)
        projects_conf.write_text("".join(new_lines), encoding="utf-8")
        click.secho(f"✓ 项目 '{name}' 已清除 Docker socket 覆盖（继承全局配置）", fg="green")
    else:
        update_conf_field(projects_conf, name, "DOCKER_SOCKET", socket)
        click.secho(f"✓ 项目 '{name}' 已设置 DOCKER_SOCKET={socket}", fg="green")
    click.secho("  执行 coderfleet apply 使配置生效", fg="yellow")


@project_group.command("enable")
@click.argument("name")
@click.pass_context
def cmd_project_enable(ctx: click.Context, name: str) -> None:
    """Enable a project (container will start on next apply)."""
    ws: Path = ctx.obj["workspace"]
    projects_conf = ws / "projects.conf"

    if not any(p.get("NAME") == name for p in parse_conf(projects_conf)):
        raise click.ClickException(f"项目 '{name}' 不存在")

    update_conf_field(projects_conf, name, "ACTIVE", "on")
    click.secho(f"✓ 项目 '{name}' 已启用", fg="green")
    click.secho("  执行 coderfleet apply 使配置生效", fg="yellow")


@project_group.command("disable")
@click.argument("name")
@click.pass_context
def cmd_project_disable(ctx: click.Context, name: str) -> None:
    """Disable a project (container will not start on next apply)."""
    ws: Path = ctx.obj["workspace"]
    projects_conf = ws / "projects.conf"

    if not any(p.get("NAME") == name for p in parse_conf(projects_conf)):
        raise click.ClickException(f"项目 '{name}' 不存在")

    update_conf_field(projects_conf, name, "ACTIVE", "off")
    click.secho(f"✓ 项目 '{name}' 已禁用", fg="green")
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
    click.echo(f"  {'名称':<20} {'账号':<20} {'状态':<8} {'IDE':<12} 路径")
    click.echo("  " + "─" * 82)

    if not projects:
        click.secho("  暂无项目，使用 coderfleet project add 添加", fg="yellow")
        click.echo()
        return

    for p in projects:
        name = p.get("NAME", "")
        account = p.get("ACCOUNT", "")
        path = p.get("PATH", "")
        ide = f":{p.get('IDE_PORT', '')}" if p.get("IDE", "").lower() == "on" else "-"
        active_raw = p.get("ACTIVE", "on").strip().lower()
        active = active_raw in {"1", "true", "yes", "on"}
        status = click.style("● 启用", fg="green") if active else click.style("○ 停用", fg="yellow")
        click.echo(f"  {name:<20} {account:<20} {status:<8} {ide:<12} {path}")

    click.echo()


# ── config ────────────────────────────────────────────────────


@click.group("config")
def config_group() -> None:
    """Global configuration management (config.conf)."""
    pass


@config_group.command("set")
@click.argument("key")
@click.argument("value")
@click.pass_context
def cmd_config_set(ctx: click.Context, key: str, value: str) -> None:
    """Set a config value (creates or updates config.conf).

    \b
    Examples:
      coderfleet config set PORT 9000
      coderfleet config set CODERFLEET_PORT 9000
    """
    ws: Path = ctx.obj["workspace"]
    # Accept bare "PORT" as alias for "CODERFLEET_PORT"
    normalized = key.upper()
    if normalized == "PORT":
        normalized = "CODERFLEET_PORT"
    set_config(ws, normalized, value)
    click.secho(f"✓ {normalized}={value}  已写入 config.conf", fg="green")
    if normalized == "CODERFLEET_PORT":
        click.secho("  重启 server 后生效：coderfleet server --stop && coderfleet server --daemon", dim=True)


@config_group.command("get")
@click.argument("key", required=False)
@click.pass_context
def cmd_config_get(ctx: click.Context, key: str | None) -> None:
    """Show one or all config values.

    \b
    Examples:
      coderfleet config get           # 显示全部
      coderfleet config get PORT
    """
    ws: Path = ctx.obj["workspace"]
    cfg = load_config(ws)
    if key:
        normalized = key.upper()
        if normalized == "PORT":
            normalized = "CODERFLEET_PORT"
        val = cfg.get(normalized)
        if val is None:
            click.secho(f"{normalized} 未设置（默认值生效）", fg="yellow")
        else:
            click.echo(f"{normalized}={val}")
    else:
        if not cfg:
            click.secho("config.conf 为空或不存在", fg="yellow")
            return
        for k, v in sorted(cfg.items()):
            click.echo(f"{k}={v}")
