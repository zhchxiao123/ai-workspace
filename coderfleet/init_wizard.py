"""
init_wizard.py — coderfleet init 交互式初始化向导

向导流程：
1. 确认工作区路径
2. 创建目录结构
3. 配置代理与镜像参数 → 写入 config.conf
4. 拷贝 Dockerfile / entrypoint.sh 到工作区
5. 创建空白 accounts.conf / projects.conf（如不存在）
6. 打印后续步骤提示
"""
from __future__ import annotations

import importlib.resources
import shutil
import sys
from pathlib import Path

import click

from coderfleet.config import ensure_workspace, get_workspace, load_config

# ── 工具函数 ──────────────────────────────────────────────


def _data_file(name: str) -> Path:
    """返回 coderfleet/data/ 中打包资源文件的路径。"""
    pkg = importlib.resources.files("coderfleet.data")
    return Path(str(pkg / name))


def _write_config(ws: Path, values: dict[str, str]) -> None:
    """将 key=value 对写入 config.conf（整体覆盖）。"""
    conf_path = ws / "config.conf"
    proxy_mode = values.get("PROXY_MODE", "host")

    lines = [
        "# ============================================================",
        "#  config.conf — CoderFleet 全局配置",
        "#  修改后执行 coderfleet apply 生效",
        "# ============================================================",
        "",
        "# ── Server 配置 ───────────────────────────────────────────",
        "# Web UI / API 监听端口（默认 8765）",
        "#CODERFLEET_PORT=8765",
        "",
        "# ── 镜像配置 ──────────────────────────────────────────────",
        f"IMAGE_NAME={values['IMAGE_NAME']}",
        f"IMAGE_TAG={values['IMAGE_TAG']}",
        f"BUILD_PLATFORM={values['BUILD_PLATFORM']}",
        "",
        "# ── Docker socket（可选，高权限）──────────────────────────",
        "# off 关闭；auto 自动识别 Colima / Docker Desktop / Linux / rootless Docker",
        "# 开启后项目容器可控制宿主机 Docker，仅用于可信镜像",
        "DOCKER_SOCKET=off",
        "DOCKER_SOCKET_TARGET=/var/run/docker.sock",
        "",
        "# ── 代理模式 ──────────────────────────────────────────────",
        "# host  → 转发至宿主机代理（Clash / V2Ray 等，默认）",
        "# xray  → 启动 Xray 容器，gost 转发给 Xray（适合无 GUI 服务器）",
        f"PROXY_MODE={proxy_mode}",
    ]

    if proxy_mode == "xray":
        lines += [
            "",
            "# ── Xray 容器配置（PROXY_MODE=xray 时生效）────────────",
            f"XRAY_IP={values.get('XRAY_IP', '172.21.0.3')}",
            f"XRAY_LISTEN_PORT={values.get('XRAY_LISTEN_PORT', '10809')}",
            f"XRAY_IMAGE={values.get('XRAY_IMAGE', 'ghcr.io/xtls/xray-core:latest')}",
            f"XRAY_CONFIG={values.get('XRAY_CONFIG', str(Path.home() / '.coderfleet' / 'xray' / 'config.json'))}",
        ]
    else:
        lines += [
            "",
            "# ── 宿主机代理配置（PROXY_MODE=host 时生效）────────────",
            "# 宿主机代理地址（容器通过 host.docker.internal 访问宿主机）",
            f"PROXY_HOST={values['PROXY_HOST']}",
            "# 混合端口（同时支持 HTTP/SOCKS5）：两个填同一个值",
            "# 分开端口（如 Clash：HTTP=7890 SOCKS5=7891）：分别填写",
            f"PROXY_HTTP_PORT={values['PROXY_HTTP_PORT']}",
            f"PROXY_SOCKS5_PORT={values['PROXY_SOCKS5_PORT']}",
        ]

    lines += [
        "",
        "# ── 内部网络配置 ──────────────────────────────────────────",
        f"INTERNAL_SUBNET={values['INTERNAL_SUBNET']}",
        f"RELAY_IP={values['RELAY_IP']}",
        "",
        "# 代理中继对内网监听的端口（容器内 HTTP_PROXY 指向此端口）",
        f"RELAY_LISTEN_PORT={values['RELAY_LISTEN_PORT']}",
        "",
        "# 代理中继镜像",
        f"RELAY_IMAGE={values['RELAY_IMAGE']}",
        "",
    ]
    conf_path.write_text("\n".join(lines), encoding="utf-8")


def _create_empty_conf(path: Path, example_name: str) -> None:
    """拷贝 example 文件作为初始 conf（保留注释，去掉示例数据行）。"""
    if path.exists():
        return
    src = _data_file(example_name)
    if src.exists():
        # 复制 example，但去掉实际数据行（非注释/非空的行）
        lines = src.read_text(encoding="utf-8").splitlines(keepends=True)
        kept = []
        for line in lines:
            stripped = line.strip()
            # 保留空行和注释行；去掉 example 末尾的示例数据行
            if not stripped or stripped.startswith("#"):
                kept.append(line)
        path.write_text("".join(kept), encoding="utf-8")
    else:
        path.write_text("", encoding="utf-8")


# ── 向导主体 ──────────────────────────────────────────────

def run_init_wizard(ws: Path) -> None:
    """交互式初始化工作区。"""
    click.echo()
    click.secho("CoderFleet 初始化向导", bold=True)
    click.echo("=" * 40)

    # ── 步骤 1：确认工作区 ──────────────────────────────
    click.echo(f"\n工作区目录：{click.style(str(ws), fg='cyan')}")
    if ws.exists() and (ws / "config.conf").exists():
        if not click.confirm("config.conf 已存在，是否重新配置？", default=False):
            click.echo("跳过配置，仅补全缺失目录和文件。")
            ensure_workspace(ws)
            _copy_runtime_files(ws)
            _init_empty_confs(ws)
            _print_next_steps(ws)
            return

    ensure_workspace(ws)
    click.secho("✓ 工作区目录已就绪", fg="green")

    # ── 步骤 2：代理配置 ────────────────────────────────
    click.echo("\n── 代理配置 ──────────────────────────────────────")
    click.echo("容器可通过代理中继访问互联网。")
    click.echo("不需要代理的账号可在 accounts.conf 中设置 PROXY=off 直连。")

    use_proxy = click.confirm("是否为容器启用代理中继？", default=False)

    proxy_mode = "host"
    proxy_host = "host.docker.internal"
    proxy_http_port = "7890"
    proxy_socks5_port = "7890"
    xray_listen_port = "10809"
    xray_image = "ghcr.io/xtls/xray-core:latest"
    xray_config_str = ""

    if use_proxy:
        click.echo()
        click.echo("  代理模式：")
        click.echo("    [1] 宿主机代理 (host) — 转发至宿主机运行的 Clash / V2Ray")
        click.echo("    [2] Xray 容器  (xray) — 启动独立 Xray 容器（适合无 GUI 服务器）")
        mode_choice = click.prompt("请选择", default="1", type=click.Choice(["1", "2"]))
        proxy_mode = "xray" if mode_choice == "2" else "host"

        if proxy_mode == "host":
            proxy_host = click.prompt("代理地址", default="host.docker.internal")
            proxy_http_port = click.prompt("代理 HTTP 端口", default="10808")
            same_port = click.confirm(
                f"SOCKS5 端口与 HTTP 端口相同（{proxy_http_port}）",
                default=True,
            )
            proxy_socks5_port = proxy_http_port if same_port else click.prompt(
                "代理 SOCKS5 端口",
                default="10808",
            )
        else:
            click.echo()
            xray_listen_port = click.prompt(
                "Xray HTTP 入站端口（需与 xray/config.json 中 inbounds[0].port 一致）",
                default="10809",
            )
            xray_image = click.prompt("Xray 镜像", default="ghcr.io/xtls/xray-core:latest")
            default_xray_cfg = str(ws / "xray" / "config.json")
            xray_config_str = click.prompt("Xray 配置文件路径", default=default_xray_cfg)
            xray_config_path = Path(xray_config_str).expanduser()
            xray_config_path.parent.mkdir(parents=True, exist_ok=True)
            if not xray_config_path.exists():
                tpl = _data_file("xray-config.json.example")
                if tpl.exists():
                    shutil.copy2(str(tpl), str(xray_config_path))
                    click.secho(f"✓ Xray 配置模板已创建：{xray_config_path}", fg="green")
                    click.secho("  请编辑该文件填入服务器信息，然后执行 coderfleet apply", fg="yellow")
                else:
                    click.secho(f"  请在 {xray_config_path} 创建 Xray 配置文件后执行 coderfleet apply", fg="yellow")
            else:
                click.secho(f"  使用已有 Xray 配置：{xray_config_path}", fg="cyan")
    else:
        click.secho("  跳过代理配置。如需启用，在 config.conf 修改，账号添加 PROXY=relay 即可。", fg="yellow")

    # ── 步骤 3：镜像配置 ────────────────────────────────
    click.echo("\n── Docker 镜像配置 ───────────────────────────────")

    import platform as _platform
    default_platform = (
        "linux/arm64" if _platform.machine() in ("arm64", "aarch64") else "linux/amd64"
    )

    image_name = click.prompt("镜像名称", default="coderfleet")
    image_tag = click.prompt("镜像标签", default="latest")
    build_platform = click.prompt("构建平台", default=default_platform)

    # ── 步骤 4：写入 config.conf ─────────────────────────
    values: dict[str, str] = {
        "IMAGE_NAME":       image_name,
        "IMAGE_TAG":        image_tag,
        "BUILD_PLATFORM":   build_platform,
        "PROXY_MODE":       proxy_mode,
        "INTERNAL_SUBNET":  "172.21.0.0/16",
        "RELAY_IP":         "172.21.0.2",
        "RELAY_LISTEN_PORT": "10808",
        "RELAY_IMAGE":      "gogost/gost:3",
    }
    if proxy_mode == "xray":
        values["XRAY_IP"]           = "172.21.0.3"
        values["XRAY_LISTEN_PORT"]  = xray_listen_port
        values["XRAY_IMAGE"]        = xray_image
        values["XRAY_CONFIG"]       = xray_config_str
    else:
        values["PROXY_HOST"]        = proxy_host
        values["PROXY_HTTP_PORT"]   = proxy_http_port
        values["PROXY_SOCKS5_PORT"] = proxy_socks5_port
    _write_config(ws, values)
    click.secho("✓ config.conf 已生成", fg="green")

    # ── 步骤 5：拷贝运行时文件 ───────────────────────────
    _copy_runtime_files(ws)

    # ── 步骤 6：初始化 conf 文件 ─────────────────────────
    _init_empty_confs(ws)

    _print_next_steps(ws)


def _copy_runtime_files(ws: Path) -> None:
    """将 Dockerfile、entrypoint.sh 和 scripts/ 拷贝到工作区（已存在则跳过）。"""
    for name in ("Dockerfile", "entrypoint.sh"):
        dst = ws / name
        if dst.exists():
            click.echo(f"  {name} 已存在，跳过")
            continue
        src = _data_file(name)
        if src.exists():
            shutil.copy2(str(src), str(dst))
            if name.endswith(".sh"):
                dst.chmod(0o755)
            click.secho(f"✓ {name} 已拷贝", fg="green")
        else:
            click.secho(f"警告：找不到内置 {name}，请手动放置", fg="yellow")

    # scripts/ — Dockerfile 构建时需要的辅助脚本
    scripts_src = Path(str(_data_file("scripts")))
    scripts_dst = ws / "scripts"
    if scripts_src.is_dir():
        scripts_dst.mkdir(exist_ok=True)
        for f in scripts_src.iterdir():
            dst = scripts_dst / f.name
            if dst.exists():
                continue
            shutil.copy2(str(f), str(dst))
        click.secho("✓ scripts/ 已拷贝", fg="green")


def _init_empty_confs(ws: Path) -> None:
    """创建空白的 accounts.conf 和 projects.conf（如不存在）。"""
    for fname, example in (
        ("accounts.conf", "accounts.conf.example"),
        ("projects.conf", "projects.conf.example"),
    ):
        path = ws / fname
        if not path.exists():
            _create_empty_conf(path, example)
            click.secho(f"✓ {fname} 已创建（空白，仅含注释）", fg="green")


def _print_next_steps(ws: Path) -> None:
    """打印后续操作提示。"""
    click.echo()
    click.secho("初始化完成！后续步骤：", bold=True)
    click.echo()
    click.echo(f"  1. 构建 Docker 镜像（首次约 5-10 分钟）：")
    click.secho(f"       coderfleet build", fg="cyan")
    click.echo(f"  2. 添加账号：")
    click.secho(f"       coderfleet account add <名称> TYPE=claude", fg="cyan")
    click.secho(f"       coderfleet account add <名称> TYPE=opencode", fg="cyan")
    click.echo(f"  3. 添加项目：")
    click.secho(f"       coderfleet project add <名称> <账号名> ~/projects/myproject", fg="cyan")
    click.echo(f"  4. 应用配置并启动容器：")
    click.secho(f"       coderfleet apply", fg="cyan")
    click.echo(f"  5. 登录账号：")
    click.secho(f"       coderfleet login <账号名>", fg="cyan")
    click.echo(f"  6. 启动 Web UI：")
    click.secho(f"       coderfleet server", fg="cyan")
    click.echo()
    click.echo(f"工作区位置：{ws}")
    if ws != Path.home() / ".coderfleet":
        click.echo(
            f"提示：非默认工作区，运行 coderfleet 命令时需设置环境变量：\n"
            f"  export CODERFLEET_WORKSPACE={ws}"
        )
    click.echo()
