"""
docker_ops.py — Docker / compose 生命周期命令

build / apply / up / down / restart / status / logs / enter / check-proxy
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Optional

import click

from coderfleet.config import load_config, parse_conf, truthy
from coderfleet.compose import write_compose, ide_service_name
from coderfleet.server.models import ImageBuild, ImageBuildStatus


# ── helpers ───────────────────────────────────────────────────


def _dc_prefix() -> list[str]:
    """Detect docker compose v2 or legacy docker-compose."""
    r = subprocess.run(["docker", "compose", "version"], capture_output=True, text=True)
    if r.returncode == 0:
        return ["docker", "compose"]
    if shutil.which("docker-compose"):
        return ["docker-compose"]
    raise click.ClickException("找不到 docker compose 或 docker-compose，请先安装 Docker")


def _dc(ws: Path) -> list[str]:
    """Full docker compose command prefix including -f flag."""
    return _dc_prefix() + ["-f", str(ws / "docker-compose.yml")]


def _container_name(project_name: str, acc_type: str) -> str:
    return f"{acc_type}-{project_name}"


def _service_name(project_name: str, acc_type: str) -> str:
    return f"{acc_type}-project-{project_name}"


def _is_running(container: str) -> bool:
    r = subprocess.run(
        ["docker", "inspect", container, "--format", "{{.State.Running}}"],
        capture_output=True, text=True,
    )
    return r.stdout.strip() == "true"


def _get_accounts(ws: Path) -> dict[str, dict[str, str]]:
    return {r["NAME"]: r for r in parse_conf(ws / "accounts.conf") if "NAME" in r}


def _get_projects(ws: Path) -> list[dict[str, str]]:
    return [p for p in parse_conf(ws / "projects.conf") if "NAME" in p and "ACCOUNT" in p]


def _run_and_record_build(cmd: list[str], image: str, ws: Path, kind: str, project_name: str = "") -> bool:
    """执行 docker build，实时回显到终端的同时把完整输出和构建记录落盘到 builds/ ——
    与 Web 端触发的构建共用同一份存储，CLI 和 Web UI 因此能在同一个「构建历史」
    列表里看到彼此的构建（见 GET /api/builds）。"""
    builds_dir = ws / "builds"
    build = ImageBuild(id=uuid.uuid4().hex, kind=kind, project_name=project_name, image_tag=image, triggered_by="cli")
    build.save(builds_dir)
    log_path = builds_dir / f"{build.id}.log"

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    assert proc.stdout is not None
    with log_path.open("w", encoding="utf-8") as f:
        for line in proc.stdout:
            click.echo(line, nl=False)
            f.write(line)
    rc = proc.wait()

    build.update_status(
        ImageBuildStatus.succeeded if rc == 0 else ImageBuildStatus.failed, builds_dir, exit_code=rc,
    )
    return rc == 0


def _service_names_for(ws: Path, project_names: tuple[str, ...]) -> list[str]:
    """Resolve project names (from projects.conf) to their compose service names.

    Includes the IDE proxy service for projects with IDE=on, so `up`/`down`/`restart
    <project>` actually bring the IDE container along instead of leaving it stale
    until a full `apply`.
    """
    accounts = _get_accounts(ws)
    projects = {p["NAME"]: p for p in _get_projects(ws)}
    services = []
    for name in project_names:
        proj = projects.get(name)
        if proj is None:
            raise click.ClickException(f"项目 '{name}' 不在 projects.conf 中")
        acc_type = accounts.get(proj.get("ACCOUNT", ""), {}).get("TYPE", "")
        services.append(_service_name(name, acc_type))
        if truthy(proj.get("IDE", "off")):
            services.append(ide_service_name(name))
    return services


def start_services(ws: Path, services: list[str]) -> subprocess.CompletedProcess:
    """Create (if needed) and start the given services, or all of them if empty.

    Shared by the `up` CLI command and the server's per-project start endpoint.
    Handles both a brand-new project (container never created) and a
    previously-stopped one through the same code path.
    """
    return subprocess.run(_dc(ws) + ["up", "-d", *services])


def stop_services(ws: Path, services: list[str]) -> subprocess.CompletedProcess:
    """Non-destructively stop the given services (container is preserved, not removed).

    Shared by the `down` CLI command and the server's per-project stop endpoint.
    """
    return subprocess.run(_dc(ws) + ["stop", *services])


# ── commands ──────────────────────────────────────────────────


@click.command("build")
@click.argument("project", required=False, default=None, metavar="[PROJECT]")
@click.option("--no-cache", is_flag=True, help="不使用构建缓存（强制完整重新构建）")
@click.option("--pull", is_flag=True, help="强制拉取最新基础镜像")
@click.option("--platform", "platform_override", default=None, metavar="PLATFORM",
              help="覆盖 config.conf 中的 BUILD_PLATFORM（如 linux/amd64）")
@click.option("--tag", "tag_override", default=None, metavar="TAG",
              help="覆盖 config.conf 中的 IMAGE_TAG（仅构建共享镜像时有效）")
@click.option("--all-projects", "all_projects", is_flag=True,
              help="构建共享镜像 + 所有有 Dockerfile 的项目专属镜像")
@click.pass_context
def cmd_build(
    ctx: click.Context,
    project: Optional[str],
    no_cache: bool,
    pull: bool,
    platform_override: Optional[str],
    tag_override: Optional[str],
    all_projects: bool,
) -> None:
    """Build the shared Docker image, or a per-project image.

    \b
    Examples:
      coderfleet build               # 构建共享镜像（原有行为）
      coderfleet build my-project    # 构建 my-project 专属镜像
      coderfleet build --all-projects  # 共享镜像 + 所有有 Dockerfile 的项目镜像
    """
    ws: Path = ctx.obj["workspace"]
    cfg = load_config(ws)
    platform = platform_override or cfg.get("BUILD_PLATFORM", "linux/amd64")

    def _build_image(image: str, dockerfile: Path, context: Path, kind: str, project_name: str = "") -> bool:
        cmd = [
            "docker", "build",
            "--platform", platform,
            "--tag", image,
            "--file", str(dockerfile),
        ]
        if no_cache:
            cmd.append("--no-cache")
        if pull:
            cmd.append("--pull")
        cmd.append(str(context))
        return _run_and_record_build(cmd, image, ws, kind, project_name)

    if project:
        # ── 构建单个项目专属镜像 ──────────────────────────────
        projects = parse_conf(ws / "projects.conf")
        proj = next((p for p in projects if p.get("NAME") == project), None)
        if proj is None:
            raise click.ClickException(f"项目 '{project}' 不在 projects.conf 中")

        dockerfile = ws / "projects" / project / "Dockerfile"
        if not dockerfile.exists():
            raise click.ClickException(
                f"找不到 {dockerfile}\n"
                f"请先在 {dockerfile.parent}/ 创建 Dockerfile，再执行此命令"
            )

        image = proj.get("IMAGE") or f"coderfleet-{project}:latest"
        click.echo(f"构建项目镜像 {image}（平台：{platform}）...")
        click.echo()

        if not _build_image(image, dockerfile, dockerfile.parent, "project", project):
            raise click.ClickException("项目镜像构建失败")

        # 若 IMAGE= 未设置则写入 projects.conf
        from coderfleet.config import update_conf_field
        if not proj.get("IMAGE"):
            update_conf_field(ws / "projects.conf", project, "IMAGE", image)
            click.secho(f"  已自动写入 IMAGE={image} 到 projects.conf", dim=True)

        click.echo()
        click.secho(f"✓ 项目镜像构建完成：{image}", fg="green")
        click.secho("  执行 coderfleet apply 使新镜像生效", fg="yellow")
        return

    if all_projects:
        # ── 构建共享镜像 + 所有有 Dockerfile 的项目镜像 ─────
        projects = parse_conf(ws / "projects.conf")
        items: list[tuple[str, Path, Path, str, str]] = []  # (image, dockerfile, context, kind, project_name)

        shared_dockerfile = ws / "Dockerfile"
        if shared_dockerfile.exists():
            image_name = cfg.get("IMAGE_NAME", "coderfleet")
            image_tag = tag_override or cfg.get("IMAGE_TAG", "latest")
            items.append((f"{image_name}:{image_tag}", shared_dockerfile, ws, "shared", ""))

        for p in projects:
            pname = p.get("NAME", "")
            if not pname:
                continue
            df = ws / "projects" / pname / "Dockerfile"
            if df.exists():
                img = p.get("IMAGE") or f"coderfleet-{pname}:latest"
                items.append((img, df, df.parent, "project", pname))

        if not items:
            raise click.ClickException("没有找到任何 Dockerfile")

        click.echo(f"构建 {len(items)} 个镜像（平台：{platform}）...")
        failed = []
        for image, dockerfile, context, kind, pname in items:
            click.secho(f"\n  → {image}", bold=True)
            if not _build_image(image, dockerfile, context, kind, pname):
                failed.append(image)

        click.echo()
        if failed:
            raise click.ClickException(f"以下镜像构建失败：{', '.join(failed)}")
        click.secho(f"✓ 全部 {len(items)} 个镜像构建完成", fg="green")
        click.secho("  执行 coderfleet apply 使新镜像生效", fg="yellow")
        return

    # ── 构建共享镜像（原有行为）──────────────────────────────
    dockerfile = ws / "Dockerfile"
    if not dockerfile.exists():
        raise click.ClickException(
            f"找不到 Dockerfile（{dockerfile}），请先执行 coderfleet init"
        )

    image_name = cfg.get("IMAGE_NAME", "coderfleet")
    image_tag = tag_override or cfg.get("IMAGE_TAG", "latest")
    image = f"{image_name}:{image_tag}"

    click.echo(f"构建镜像 {image}（平台：{platform}）...")
    if no_cache:
        click.secho("  --no-cache：跳过所有缓存层", fg="yellow")
    if pull:
        click.secho("  --pull：强制拉取最新基础镜像", fg="yellow")
    click.secho("首次构建约需 5~10 分钟，请耐心等待", dim=True)
    click.echo()

    if not _build_image(image, dockerfile, ws, "shared"):
        raise click.ClickException("镜像构建失败")

    click.echo()
    click.secho(f"✓ 镜像构建完成：{image}", fg="green")


_BUILD_STATUS_STYLE = {
    "running":   ("yellow", "○ 执行中"),
    "succeeded": ("green",  "✓ 成功  "),
    "failed":    ("red",    "✗ 失败  "),
    "cancelled": ("yellow", "⊘ 已停止"),
}


@click.command("build-history")
@click.option("--project", "project_filter", default=None, help="只看指定项目的构建记录")
@click.pass_context
def cmd_build_history(ctx: click.Context, project_filter: Optional[str]) -> None:
    """List past image builds (CLI 和 Web UI 触发的都会出现在同一份历史里)."""
    ws: Path = ctx.obj["workspace"]
    builds = ImageBuild.load_all(ws / "builds")
    if project_filter:
        builds = [b for b in builds if b.project_name == project_filter]
    if not builds:
        click.echo("暂无构建记录")
        return
    for b in builds:
        color, label = _BUILD_STATUS_STYLE.get(b.status.value, ("white", b.status.value))
        target = b.project_name or "(共享镜像)"
        click.secho(f"{b.id}  ", nl=False, dim=True)
        click.secho(label, fg=color, nl=False)
        click.echo(f"  {b.image_tag:<32s}  {target:<20s}  {b.created}  [{b.triggered_by}]")


@click.command("build-logs")
@click.argument("build_id")
@click.option("-f", "--follow", is_flag=True, help="持续输出仍在进行中的构建的后续日志")
@click.pass_context
def cmd_build_logs(ctx: click.Context, build_id: str, follow: bool) -> None:
    """Print (or, with -f, follow) the log of a specific image build."""
    ws: Path = ctx.obj["workspace"]
    builds_dir = ws / "builds"
    log_path = builds_dir / f"{build_id}.log"
    if not log_path.exists():
        raise click.ClickException(f"找不到构建 '{build_id}' 的日志")

    with log_path.open("r", encoding="utf-8") as f:
        click.echo(f.read(), nl=False)
        if not follow:
            return
        while True:
            record_path = builds_dir / f"{build_id}.json"
            build = ImageBuild.load(record_path) if record_path.exists() else None
            line = f.readline()
            if line:
                click.echo(line, nl=False)
                continue
            if build is None or build.status != ImageBuildStatus.running:
                return
            time.sleep(0.3)


@click.command("apply")
@click.option("--full", is_flag=True,
              help="销毁并强制重建所有容器（旧行为），而不是增量同步")
@click.pass_context
def cmd_apply(ctx: click.Context, full: bool) -> None:
    """Regenerate docker-compose.yml and reconcile containers.

    By default this only creates/recreates the services that actually
    changed (new project, edited config, removed project) and leaves every
    other running container untouched. Pass --full to fall back to the old
    behavior of tearing down and force-recreating every container.
    """
    ws: Path = ctx.obj["workspace"]

    click.echo("生成 docker-compose.yml...")
    write_compose(ws)
    click.secho("✓ docker-compose.yml 已生成", fg="green")
    click.echo()

    dc = _dc(ws)
    if full:
        click.secho(
            "⚠ --full：将销毁并重建所有容器，所有正在运行的会话都会被中断",
            fg="yellow",
        )
        click.echo("重启容器以应用新配置...")
        subprocess.run(dc + ["down", "--remove-orphans"])
        result = subprocess.run(dc + ["up", "-d", "--force-recreate"])
    else:
        click.echo("同步容器状态（仅新增/变更的项目会受影响）...")
        result = subprocess.run(dc + ["up", "-d", "--remove-orphans"])

    if result.returncode != 0:
        raise click.ClickException("容器启动失败")

    click.echo()
    click.secho("✓ 完成！使用 coderfleet status 查看状态", fg="green")


@click.command("up")
@click.argument("projects", nargs=-1, metavar="[PROJECT...]")
@click.pass_context
def cmd_up(ctx: click.Context, projects: tuple[str, ...]) -> None:
    """Start all containers, or just the named project(s).

    \b
    Examples:
      coderfleet up               # 启动所有未运行的容器（不影响已运行的）
      coderfleet up my-project     # 只启动/创建 my-project 的容器
    """
    ws: Path = ctx.obj["workspace"]
    compose_file = ws / "docker-compose.yml"

    if projects:
        # 确保这些项目（可能是刚新增的）的服务定义已经写入 compose 文件
        write_compose(ws)
        services = _service_names_for(ws, projects)
        click.echo(f"启动项目：{', '.join(projects)}...")
        result = start_services(ws, services)
    else:
        if not compose_file.exists():
            click.secho("docker-compose.yml 不存在，先生成...", fg="yellow")
            write_compose(ws)
            click.echo()
        click.echo("启动所有容器...")
        result = start_services(ws, [])

    if result.returncode == 0:
        click.secho("✓ 启动完成", fg="green")
    else:
        raise click.ClickException("启动失败")


@click.command("down")
@click.argument("projects", nargs=-1, metavar="[PROJECT...]")
@click.pass_context
def cmd_down(ctx: click.Context, projects: tuple[str, ...]) -> None:
    """Stop all containers, or just the named project(s).

    \b
    Examples:
      coderfleet down              # 停止并移除所有容器
      coderfleet down my-project    # 只停止 my-project 的容器（保留容器，不删除）
    """
    ws: Path = ctx.obj["workspace"]

    if projects:
        services = _service_names_for(ws, projects)
        click.echo(f"停止项目：{', '.join(projects)}...")
        stop_services(ws, services)
    else:
        click.echo("停止所有容器...")
        subprocess.run(_dc(ws) + ["down"])
    click.secho("✓ 已停止", fg="green")


@click.command("restart")
@click.argument("projects", nargs=-1, metavar="[PROJECT...]")
@click.pass_context
def cmd_restart(ctx: click.Context, projects: tuple[str, ...]) -> None:
    """Restart all containers, or just the named project(s)."""
    ws: Path = ctx.obj["workspace"]
    dc = _dc(ws)

    if projects:
        services = _service_names_for(ws, projects)
        click.echo(f"重启项目：{', '.join(projects)}...")
        stop_services(ws, services)
        result = start_services(ws, services)
    else:
        click.echo("重启...")
        subprocess.run(dc + ["down"])
        result = subprocess.run(dc + ["up", "-d", "--force-recreate"])

    if result.returncode == 0:
        click.secho("✓ 重启完成", fg="green")
    else:
        raise click.ClickException("重启失败")


@click.command("status")
@click.pass_context
def cmd_status(ctx: click.Context) -> None:
    """Show container and image status."""
    ws: Path = ctx.obj["workspace"]
    cfg = load_config(ws)
    accounts = _get_accounts(ws)
    projects = _get_projects(ws)

    click.echo()
    click.echo("  ── 项目与容器状态 " + "─" * 50)

    for p in projects:
        pname = p["NAME"]
        paccount = p.get("ACCOUNT", "")
        acc = accounts.get(paccount, {})
        acc_type = acc.get("TYPE", "")
        ctr = _container_name(pname, acc_type) if acc_type else pname

        if _is_running(ctr):
            status = click.style("● 运行中", fg="green")
        else:
            status = click.style("○ 已停止", fg="red")

        click.echo(f"  {pname:<20} [{acc_type:<6}] {status}  账号：{paccount}")

    click.echo()
    click.echo("  ── 代理中继 " + "─" * 56)
    r = subprocess.run(
        ["docker", "inspect", "coderfleet-proxy-relay",
         "--format", "{{.State.Health.Status}}"],
        capture_output=True, text=True,
    )
    health = r.stdout.strip() if r.returncode == 0 else "未运行"
    if health == "healthy":
        click.echo(f"  coderfleet-proxy-relay: {click.style(health, fg='green')}")
    elif health == "starting":
        click.echo(f"  coderfleet-proxy-relay: {click.style(health + '（启动中）', fg='yellow')}")
    else:
        click.echo(f"  coderfleet-proxy-relay: {click.style(health, fg='red')}")

    click.echo()
    click.echo("  ── 镜像信息 " + "─" * 56)
    image = f"{cfg.get('IMAGE_NAME', 'coderfleet')}:{cfg.get('IMAGE_TAG', 'latest')}"
    ri = subprocess.run(
        ["docker", "image", "inspect", image,
         "--format", "{{.Created}}\t{{.Size}}"],
        capture_output=True, text=True,
    )
    if ri.returncode == 0 and ri.stdout.strip():
        created, size_str = ri.stdout.strip().split("\t", 1)
        try:
            size_gb = int(size_str) / 1024 / 1024 / 1024
            size_label = f"{size_gb:.1f} GB"
        except ValueError:
            size_label = size_str
        click.echo(
            f"  {image}: {click.style('已构建', fg='green')}"
            f"（创建于 {created[:10]}，大小 {size_label}）"
        )
    else:
        click.echo(
            f"  {image}: {click.style('未构建', fg='red')}，请执行 coderfleet build"
        )
    click.echo()


@click.command("logs")
@click.argument("project", required=False)
@click.pass_context
def cmd_logs(ctx: click.Context, project: Optional[str]) -> None:
    """Stream container logs (all containers, or a specific project)."""
    ws: Path = ctx.obj["workspace"]
    dc = _dc(ws)

    if not project:
        subprocess.run(dc + ["logs", "-f"])
        return

    accounts = _get_accounts(ws)
    for p in _get_projects(ws):
        if p["NAME"] != project:
            continue
        paccount = p.get("ACCOUNT", "")
        acc_type = accounts.get(paccount, {}).get("TYPE", "")
        svc = _service_name(project, acc_type)
        subprocess.run(dc + ["logs", "-f", svc])
        return

    raise click.ClickException(f"项目 '{project}' 不在 projects.conf 中")


@click.command("enter")
@click.argument("project")
@click.pass_context
def cmd_enter(ctx: click.Context, project: str) -> None:
    """Enter a container shell (replaces current process)."""
    ws: Path = ctx.obj["workspace"]
    accounts = _get_accounts(ws)

    for p in _get_projects(ws):
        if p["NAME"] != project:
            continue
        paccount = p.get("ACCOUNT", "")
        acc_type = accounts.get(paccount, {}).get("TYPE", "")
        ctr = _container_name(project, acc_type)

        if not _is_running(ctr):
            raise click.ClickException(f"容器 {ctr} 未运行，请先执行：coderfleet up")

        click.secho(f"进入 {ctr}（类型：{acc_type}）...", dim=True)
        os.execvp("docker", ["docker", "exec", "-it", ctr, "bash"])

    raise click.ClickException(f"项目 '{project}' 不在 projects.conf 中")


@click.command("check-proxy")
@click.pass_context
def cmd_check_proxy(ctx: click.Context) -> None:
    """Check proxy connectivity for all containers."""
    ws: Path = ctx.obj["workspace"]
    cfg = load_config(ws)
    accounts = _get_accounts(ws)
    projects = _get_projects(ws)

    relay_ip = cfg.get("RELAY_IP", "172.21.0.2")
    relay_port = cfg.get("RELAY_LISTEN_PORT", "7890")

    click.echo()
    click.echo("  ── 代理连通性（应全部通）" + "─" * 43)
    for p in projects:
        pname = p["NAME"]
        paccount = p.get("ACCOUNT", "")
        acc_type = accounts.get(paccount, {}).get("TYPE", "")
        ctr = _container_name(pname, acc_type)

        click.echo(f"  {ctr:<28} → proxy-relay: ", nl=False)
        if not _is_running(ctr):
            click.secho("容器未运行", fg="yellow")
            continue
        r = subprocess.run(
            ["docker", "exec", ctr, "sh", "-c", f"nc -z {relay_ip} {relay_port}"],
            capture_output=True,
        )
        if r.returncode == 0:
            click.secho("通", fg="green")
        else:
            click.secho("不通", fg="red")

    click.echo()
    click.echo("  ── 直连公网封锁（应全部封锁）" + "─" * 40)
    for p in projects:
        pname = p["NAME"]
        paccount = p.get("ACCOUNT", "")
        acc_type = accounts.get(paccount, {}).get("TYPE", "")
        ctr = _container_name(pname, acc_type)

        click.echo(f"  {ctr:<28} → 8.8.8.8:443: ", nl=False)
        if not _is_running(ctr):
            click.secho("容器未运行", fg="yellow")
            continue
        r = subprocess.run(
            ["docker", "exec", ctr, "sh", "-c", "timeout 3 nc -z 8.8.8.8 443 2>/dev/null"],
            capture_output=True,
        )
        if r.returncode == 0:
            click.secho("可直连 ← 隔离未生效！", fg="red")
        else:
            click.secho("已封锁", fg="green")

    click.echo()
    click.echo("  ── DNS 解析（经 relay 转发，应全部通）" + "─" * 30)
    for p in projects:
        pname = p["NAME"]
        paccount = p.get("ACCOUNT", "")
        acc_type = accounts.get(paccount, {}).get("TYPE", "")
        ctr = _container_name(pname, acc_type)

        click.echo(f"  {ctr:<28} → resolve github.com: ", nl=False)
        if not _is_running(ctr):
            click.secho("容器未运行", fg="yellow")
            continue
        r = subprocess.run(
            ["docker", "exec", ctr, "sh", "-c", "getent hosts github.com"],
            capture_output=True,
        )
        if r.returncode == 0:
            click.secho("通", fg="green")
        else:
            click.secho("失败", fg="red")

    click.echo()
    click.echo("  ── 代理出口 IP " + "─" * 52)
    for p in projects:
        pname = p["NAME"]
        paccount = p.get("ACCOUNT", "")
        acc_type = accounts.get(paccount, {}).get("TYPE", "")
        ctr = _container_name(pname, acc_type)

        click.echo(f"  {ctr:<28} 出口 IP: ", nl=False)
        if not _is_running(ctr):
            click.secho("容器未运行", fg="yellow")
            continue
        r = subprocess.run(
            ["docker", "exec", ctr, "sh", "-c",
             "curl -s --max-time 6 https://api.ipify.org 2>/dev/null"],
            capture_output=True, text=True,
        )
        ip = r.stdout.strip()
        if ip:
            click.secho(ip, fg="green")
        else:
            click.secho("请求失败", fg="yellow")

    click.echo()
