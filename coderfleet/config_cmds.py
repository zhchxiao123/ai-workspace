"""
config_cmds.py — coderfleet account / project 子命令
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Optional

import click

from coderfleet.config import container_timezone, ensure_bind_mount_dir, ensure_workspace, load_config, parse_conf, remove_conf_entry, set_config, update_conf_field, write_conf_line
from coderfleet.account_type_registry import ACCOUNT_TYPES, duplicate_account_types, env_auth_type_ids
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


def _validate_account_bindings(
    accounts_conf_records: list[dict[str, str]], primary: str, secondaries: list[str],
) -> None:
    """校验 primary + secondaries 绑定的账号集合：每个账号名必须存在，且
    彼此的 TYPE 两两不同（每种 TYPE 在容器内只有一个固定挂载路径）。"""
    types_by_name = {r["NAME"]: r["TYPE"] for r in accounts_conf_records if "NAME" in r and "TYPE" in r}
    names = [primary, *secondaries]
    for n in names:
        if n not in types_by_name:
            raise click.ClickException(f"账号 '{n}' 不存在")
    dupes = duplicate_account_types([types_by_name[n] for n in names])
    if dupes:
        raise click.ClickException(
            f"账号类型互斥：{' / '.join(dupes)} 类型的账号在同一项目中只能绑定一个"
        )


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
    ensure_bind_mount_dir(ws / "accounts" / name)

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


def _format_usage_window(label: str, window) -> str:
    if window is None or window.utilization is None:
        return f"  {label:<12} 无数据"
    pct = f"{window.utilization:.0f}%"
    reset = ""
    if window.resets_at:
        reset = f"，{window.resets_at} 重置"
    return f"  {label:<12} 已用 {pct}{reset}"


_USAGE_ERROR_HINTS = {
    "no_credentials":      "找不到登录凭据，先执行 coderfleet login <账号名>",
    "unauthorized":        "token 已过期，等账号被任务用到自动刷新后重试，或重新 login",
    "rate_limited":        "被 Anthropic 限流了，稍后再试",
    "no_running_container": "这个账号当前没有正在运行的项目容器，先 coderfleet up 对应项目",
}


def _find_running_container_for_account(projects_conf: Path, acc_name: str, acc_type: str) -> Optional[str]:
    """探测必须从账号所在容器打出去（走 gost 代理），不能从宿主机裸连——
    否则同一账号的流量一会儿走代理一会儿裸连，容易触发风控。"""
    for p in parse_conf(projects_conf):
        if p.get("ACCOUNT") != acc_name:
            continue
        container = _container_name(p.get("NAME", ""), acc_type)
        if _is_running(container):
            return container
    return None


_USAGE_ELIGIBLE_TYPES = ("claude", "codex")


@account_group.command("usage")
@click.argument("name", required=False)
@click.option("--debug", is_flag=True, help="打印原始探测结果，便于排查解析问题")
@click.pass_context
def cmd_account_usage(ctx: click.Context, name: Optional[str], debug: bool) -> None:
    """查看 Claude Max / ChatGPT (Codex) 套餐用量（仅对 AUTH=login 的账号有效）。

    探测请求从账号当前正在运行的项目容器里发出（走该容器的 gost 代理），
    不会从宿主机直连 —— 账号需要有至少一个项目容器正在运行。
    """
    from coderfleet import usage_probe

    ws: Path = ctx.obj["workspace"]
    accounts_conf = ws / "accounts.conf"
    projects_conf = ws / "projects.conf"
    accounts = parse_conf(accounts_conf)
    eligible = [
        r for r in accounts
        if r.get("TYPE") in _USAGE_ELIGIBLE_TYPES and r.get("AUTH", "login") == "login"
    ]
    if name:
        eligible = [r for r in eligible if r.get("NAME") == name]
        if not eligible:
            raise click.ClickException(f"账号 '{name}' 不存在，或不是 TYPE=claude/codex AUTH=login 的账号")

    if not eligible:
        click.secho("  没有可查用量的账号（需要 TYPE=claude 或 codex，且 AUTH=login）", fg="yellow")
        return

    click.echo()
    for rec in eligible:
        acc_name = rec["NAME"]
        acc_type = rec.get("TYPE", "claude")
        click.echo(f"  ── {acc_name} " + "─" * max(1, 58 - len(acc_name)))
        container = _find_running_container_for_account(projects_conf, acc_name, acc_type)
        if container is None:
            usage = None
            click.secho(f"  探测失败：{_USAGE_ERROR_HINTS['no_running_container']}", fg="red")
        elif acc_type == "codex":
            usage = usage_probe.probe_codex_via_container(container)
        else:
            usage = usage_probe.probe_via_container(container)
        if usage is not None:
            if usage.error:
                hint = _USAGE_ERROR_HINTS.get(usage.error, usage.error)
                click.secho(f"  探测失败：{hint}", fg="red")
            else:
                if usage.subscription_type:
                    click.echo(f"  套餐：{usage.subscription_type}")
                click.echo(_format_usage_window("5 小时窗口", usage.five_hour))
                click.echo(_format_usage_window("7 天窗口", usage.seven_day))
                if usage.seven_day_opus:
                    click.echo(_format_usage_window("7 天 Opus", usage.seven_day_opus))
                if usage.seven_day_sonnet:
                    click.echo(_format_usage_window("7 天 Sonnet", usage.seven_day_sonnet))
            if debug:
                click.secho(f"  raw: {usage.model_dump_json()}", dim=True)
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
@click.option("--secondary", "secondary_accounts", multiple=True, metavar="ACCOUNT",
              help="绑定一个从账号（仅挂载认证目录/env，不参与任务调度），可重复传入以绑定多个")
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
    secondary_accounts: tuple[str, ...],
) -> None:
    """Add a new project."""
    ws: Path = ctx.obj["workspace"]

    if not _NAME_RE.match(name):
        raise click.ClickException("项目名称只能包含字母、数字、连字符")

    accounts_conf = ws / "accounts.conf"
    accounts_conf_records = parse_conf(accounts_conf)
    _validate_account_bindings(accounts_conf_records, account, list(secondary_accounts))
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
    if secondary_accounts:
        tokens["SECONDARY_ACCOUNTS"] = ",".join(secondary_accounts)
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


def _project_record(projects_conf: Path, name: str) -> dict[str, str]:
    for p in parse_conf(projects_conf):
        if p.get("NAME") == name:
            return p
    raise click.ClickException(f"项目 '{name}' 不存在")


def _write_secondary_accounts(projects_conf: Path, name: str, secondaries: list[str]) -> None:
    if secondaries:
        update_conf_field(projects_conf, name, "SECONDARY_ACCOUNTS", ",".join(secondaries))
        return
    # 清空：把 SECONDARY_ACCOUNTS= 这个 token 从该行移除
    lines = projects_conf.read_text(encoding="utf-8").splitlines(keepends=True)
    new_lines = []
    name_pat = re.compile(rf"\bNAME={re.escape(name)}(\s|$)")
    for line in lines:
        if name_pat.search(line):
            tokens = [t for t in line.rstrip("\n").split() if not t.upper().startswith("SECONDARY_ACCOUNTS=")]
            new_lines.append("  ".join(tokens) + "\n")
        else:
            new_lines.append(line)
    projects_conf.write_text("".join(new_lines), encoding="utf-8")


@project_group.command("add-secondary")
@click.argument("name")
@click.argument("account")
@click.pass_context
def cmd_project_add_secondary(ctx: click.Context, name: str, account: str) -> None:
    """Bind a secondary account to an existing project (credentials mounted only, never used for tasks)."""
    ws: Path = ctx.obj["workspace"]
    projects_conf = ws / "projects.conf"
    project = _project_record(projects_conf, name)

    secondaries = [s.strip() for s in project.get("SECONDARY_ACCOUNTS", "").split(",") if s.strip()]
    if account in secondaries:
        raise click.ClickException(f"账号 '{account}' 已是项目 '{name}' 的从账号")
    secondaries.append(account)

    accounts_conf_records = parse_conf(ws / "accounts.conf")
    _validate_account_bindings(accounts_conf_records, project["ACCOUNT"], secondaries)

    _write_secondary_accounts(projects_conf, name, secondaries)
    click.secho(f"✓ 项目 '{name}' 已绑定从账号 '{account}'", fg="green")
    click.secho("  执行 coderfleet apply 使配置生效", fg="yellow")


@project_group.command("remove-secondary")
@click.argument("name")
@click.argument("account")
@click.pass_context
def cmd_project_remove_secondary(ctx: click.Context, name: str, account: str) -> None:
    """Unbind a secondary account from an existing project."""
    ws: Path = ctx.obj["workspace"]
    projects_conf = ws / "projects.conf"
    project = _project_record(projects_conf, name)

    secondaries = [s.strip() for s in project.get("SECONDARY_ACCOUNTS", "").split(",") if s.strip()]
    if account not in secondaries:
        raise click.ClickException(f"账号 '{account}' 不是项目 '{name}' 的从账号")
    secondaries.remove(account)

    _write_secondary_accounts(projects_conf, name, secondaries)
    click.secho(f"✓ 项目 '{name}' 已解绑从账号 '{account}'", fg="green")
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
    if normalized == "CONTAINER_TIMEZONE":
        try:
            value = container_timezone({normalized: value})
        except ValueError as err:
            raise click.ClickException(str(err)) from err
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
