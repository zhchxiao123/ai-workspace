"""
docker_ops.py — Docker / compose 生命周期命令

build / apply / up / down / restart / status / logs / enter / check-proxy
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

import click

from aicm.config import load_config, parse_conf
from aicm.compose import write_compose


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


# ── commands ──────────────────────────────────────────────────


@click.command("build")
@click.pass_context
def cmd_build(ctx: click.Context) -> None:
    """Build the Docker image."""
    ws: Path = ctx.obj["workspace"]
    cfg = load_config(ws)

    dockerfile = ws / "Dockerfile"
    if not dockerfile.exists():
        raise click.ClickException(
            f"找不到 Dockerfile（{dockerfile}），请先执行 aicm init"
        )

    image = f"{cfg.get('IMAGE_NAME', 'ai-code-manager')}:{cfg.get('IMAGE_TAG', 'latest')}"
    platform = cfg.get("BUILD_PLATFORM", "linux/amd64")

    click.echo(f"构建镜像 {image}（平台：{platform}）...")
    click.secho("首次构建约需 5~10 分钟，请耐心等待", dim=True)
    click.echo()

    result = subprocess.run([
        "docker", "build",
        "--platform", platform,
        "--tag", image,
        "--file", str(dockerfile),
        str(ws),
    ])

    if result.returncode != 0:
        raise click.ClickException("镜像构建失败")

    click.echo()
    click.secho(f"✓ 镜像构建完成：{image}", fg="green")


@click.command("apply")
@click.pass_context
def cmd_apply(ctx: click.Context) -> None:
    """Regenerate docker-compose.yml and restart all containers."""
    ws: Path = ctx.obj["workspace"]

    click.echo("生成 docker-compose.yml...")
    write_compose(ws)
    click.secho("✓ docker-compose.yml 已生成", fg="green")
    click.echo()

    click.echo("重启容器以应用新配置...")
    dc = _dc(ws)
    subprocess.run(dc + ["down", "--remove-orphans"])
    result = subprocess.run(dc + ["up", "-d"])

    if result.returncode != 0:
        raise click.ClickException("容器启动失败")

    click.echo()
    click.secho("✓ 完成！使用 aicm status 查看状态", fg="green")


@click.command("up")
@click.pass_context
def cmd_up(ctx: click.Context) -> None:
    """Start all containers."""
    ws: Path = ctx.obj["workspace"]
    compose_file = ws / "docker-compose.yml"

    if not compose_file.exists():
        click.secho("docker-compose.yml 不存在，先生成...", fg="yellow")
        write_compose(ws)
        click.echo()

    click.echo("启动所有容器...")
    result = subprocess.run(_dc(ws) + ["up", "-d"])
    if result.returncode == 0:
        click.secho("✓ 启动完成", fg="green")
    else:
        raise click.ClickException("启动失败")


@click.command("down")
@click.pass_context
def cmd_down(ctx: click.Context) -> None:
    """Stop all containers."""
    ws: Path = ctx.obj["workspace"]
    click.echo("停止所有容器...")
    subprocess.run(_dc(ws) + ["down"])
    click.secho("✓ 已停止", fg="green")


@click.command("restart")
@click.pass_context
def cmd_restart(ctx: click.Context) -> None:
    """Restart all containers."""
    ws: Path = ctx.obj["workspace"]
    click.echo("重启...")
    dc = _dc(ws)
    subprocess.run(dc + ["down"])
    result = subprocess.run(dc + ["up", "-d"])
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
        ["docker", "inspect", "aicm-proxy-relay",
         "--format", "{{.State.Health.Status}}"],
        capture_output=True, text=True,
    )
    health = r.stdout.strip() if r.returncode == 0 else "未运行"
    if health == "healthy":
        click.echo(f"  aicm-proxy-relay: {click.style(health, fg='green')}")
    elif health == "starting":
        click.echo(f"  aicm-proxy-relay: {click.style(health + '（启动中）', fg='yellow')}")
    else:
        click.echo(f"  aicm-proxy-relay: {click.style(health, fg='red')}")

    click.echo()
    click.echo("  ── 镜像信息 " + "─" * 56)
    image = f"{cfg.get('IMAGE_NAME', 'ai-code-manager')}:{cfg.get('IMAGE_TAG', 'latest')}"
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
            f"  {image}: {click.style('未构建', fg='red')}，请执行 aicm build"
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
            raise click.ClickException(f"容器 {ctr} 未运行，请先执行：aicm up")

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
