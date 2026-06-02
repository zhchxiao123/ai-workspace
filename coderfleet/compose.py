"""
compose.py — docker-compose.yml 生成器

将 accounts.conf / projects.conf / config.conf 翻译为 docker-compose.yml。
per-type 信息（认证目录、环境变量）从账号类型注册表读取，无需手动维护。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import click
import yaml

from coderfleet.config import load_config, parse_conf
from coderfleet.account_type_registry import ACCOUNT_TYPES
from coderfleet.ports import allocate_ide_port


def _truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _parse_port(value: str, project_name: str) -> int:
    try:
        port = int(value)
    except (TypeError, ValueError):
        raise click.ClickException(f"项目 {project_name} 启用 IDE 时必须设置有效 IDE_PORT")
    if port < 1024 or port > 65535:
        raise click.ClickException(f"项目 {project_name} 的 IDE_PORT 必须在 1024-65535 之间")
    return port


def _make_dumper() -> type[yaml.Dumper]:
    """Return a YAML Dumper that quotes YAML-1.1 boolean-ambiguous strings."""
    _BOOL_LIKE = frozenset(["off", "on", "yes", "no", "true", "false", "null", "~"])

    class QuotedDumper(yaml.Dumper):
        pass

    def _str_repr(dumper: yaml.Dumper, data: str) -> yaml.ScalarNode:
        if data.lower() in _BOOL_LIKE:
            return dumper.represent_scalar("tag:yaml.org,2002:str", data, style='"')
        return dumper.represent_scalar("tag:yaml.org,2002:str", data)

    QuotedDumper.add_representer(str, _str_repr)
    return QuotedDumper


def generate_compose(ws: Path) -> dict[str, Any]:
    """Build docker-compose data dict from workspace config files."""
    cfg = load_config(ws)
    projects = [
        p for p in parse_conf(ws / "projects.conf")
        if "NAME" in p and "ACCOUNT" in p and "PATH" in p
    ]
    if not projects:
        raise click.ClickException("projects.conf 中没有有效项目")

    accounts = {r["NAME"]: r for r in parse_conf(ws / "accounts.conf") if "NAME" in r}

    image          = f"{cfg.get('IMAGE_NAME', 'coderfleet')}:{cfg.get('IMAGE_TAG', 'latest')}"
    subnet         = cfg.get("INTERNAL_SUBNET", "172.21.0.0/16")
    relay_ip       = cfg.get("RELAY_IP", "172.21.0.2")
    relay_port     = cfg.get("RELAY_LISTEN_PORT", "7890")
    proxy_host     = cfg.get("PROXY_HOST", "host.docker.internal")
    http_port      = cfg.get("PROXY_HTTP_PORT", "7890")
    relay_image    = cfg.get("RELAY_IMAGE", "gogost/gost:3")
    ide_proxy_image = cfg.get("IDE_PROXY_IMAGE", "alpine/socat:latest")
    build_platform = cfg.get("BUILD_PLATFORM", "linux/amd64")

    proxy_url = f"http://{relay_ip}:{relay_port}"
    no_proxy  = f"localhost,127.0.0.1,{subnet}"

    services: dict[str, Any] = {}

    services["proxy-relay"] = {
        "image": relay_image,
        "container_name": "coderfleet-proxy-relay",
        "restart": "unless-stopped",
        "networks": {
            "intnet": {"ipv4_address": relay_ip},
            "extnet": {},
        },
        "extra_hosts": ["host.docker.internal:host-gateway"],
        "command": f"-L http://:{relay_port} -F \"{proxy_host}:{http_port}\"",
        "healthcheck": {
            "test": ["CMD", "sh", "-c", f"nc -z localhost {relay_port}"],
            "interval": "8s",
            "timeout": "4s",
            "retries": 5,
            "start_period": "5s",
        },
    }

    count = 0
    used_ide_ports: set[int] = set()
    for p in projects:
        pname    = p["NAME"]
        paccount = p["ACCOUNT"]
        ppath    = str(Path(p["PATH"]).expanduser())
        ide_enabled = _truthy(p.get("IDE", "off"))

        if not _truthy(p.get("ACTIVE", "on")):
            click.secho(f"  跳过项目 {pname}（ACTIVE=off）", fg="yellow")
            continue

        acc = accounts.get(paccount)
        if not acc:
            click.secho(f"  警告：跳过项目 {pname}：账号 {paccount} 不存在", fg="yellow")
            continue

        acc_type     = acc.get("TYPE", "codex")
        acc_auth     = acc.get("AUTH", "login")
        acc_env_file = acc.get("ENV_FILE", "")
        acc_proxy    = acc.get("PROXY", "relay")

        # 从注册表查 per-type 信息（不存在则回退到 codex 默认值）
        spec     = ACCOUNT_TYPES.get(acc_type, ACCOUNT_TYPES["codex"])
        auth_dst = spec.auth_dir

        svc_name = f"{acc_type}-project-{pname}"
        ctr_name = f"{acc_type}-{pname}"
        auth_src = f"./accounts/{paccount}"

        (ws / "accounts" / paccount).mkdir(parents=True, exist_ok=True)

        # 基础环境变量（所有类型共享）
        environment: dict[str, str] = {
            "CODEX_HOME":              "/home/byclaw/.codex",
            "CLAUDE_CONFIG_DIR":       "/home/byclaw/.claude",
            "CODERFLEET_ACCOUNT_NAME": paccount,
            "CODERFLEET_ACCOUNT_TYPE": acc_type,
            "CODERFLEET_ACCOUNT_AUTH": acc_auth,
            "CODERFLEET_ACCOUNT_PROXY": acc_proxy,
        }
        # per-type 额外环境变量（从注册表读取，无需手动 if/elif）
        environment.update(spec.env_vars)

        if acc_proxy != "off":
            environment.update({
                "HTTP_PROXY":            proxy_url,
                "HTTPS_PROXY":           proxy_url,
                "http_proxy":            proxy_url,
                "https_proxy":           proxy_url,
                "ALL_PROXY":             proxy_url,
                "all_proxy":             proxy_url,
                "NO_PROXY":              no_proxy,
                "no_proxy":              no_proxy,
                "CODERFLEET_RELAY_IP":   relay_ip,
                "CODERFLEET_RELAY_PORT": relay_port,
            })

        svc: dict[str, Any] = {
            "image":        image,
            "platform":     build_platform,
            "pull_policy":  "never",
            "container_name": ctr_name,
            "restart":      "unless-stopped",
            "networks":     {"intnet": {}} if acc_proxy != "off" else {"extnet": {}},
            "environment":  environment,
            "volumes": [
                f"{auth_src}:{auth_dst}",
                f"{ppath}:/workspace",
            ],
            "working_dir": "/workspace",
        }

        if ide_enabled:
            ide_port = (
                _parse_port(p["IDE_PORT"], pname)
                if p.get("IDE_PORT")
                else None
            )
            if ide_port is None:
                try:
                    ide_port = allocate_ide_port(used_ide_ports)
                except RuntimeError as e:
                    raise click.ClickException(str(e))
            if ide_port in used_ide_ports:
                raise click.ClickException(f"IDE_PORT {ide_port} 被多个项目重复使用")
            used_ide_ports.add(ide_port)
            environment.update({
                "CODERFLEET_IDE": "on",
                "CODERFLEET_IDE_PORT": "8080",
                "CODERFLEET_IDE_AUTH": "none",
            })

        if acc_auth == "env" and acc_env_file and acc_env_file != "-":
            svc["env_file"] = [acc_env_file]

        if acc_proxy != "off":
            svc["depends_on"] = {"proxy-relay": {"condition": "service_healthy"}}

        services[svc_name] = svc

        if ide_enabled:
            ide_svc_name = f"ide-project-{pname}"
            ide_ctr_name = f"coderfleet-ide-{pname}"
            ide_networks = {"extnet": {}}
            if acc_proxy != "off":
                ide_networks["intnet"] = {}
            services[ide_svc_name] = {
                "image": ide_proxy_image,
                "container_name": ide_ctr_name,
                "restart": "unless-stopped",
                "networks": ide_networks,
                "ports": [f"127.0.0.1:{ide_port}:8080"],
                "entrypoint": ["socat"],
                "command": [
                    "TCP-LISTEN:8080,fork,reuseaddr",
                    f"TCP:{svc_name}:8080",
                ],
                "depends_on": {
                    svc_name: {"condition": "service_started"},
                },
            }
        count += 1
        click.secho(f"  [{acc_type}] {pname}（账号：{paccount}，代理：{acc_proxy}）", fg="cyan")

    if count == 0:
        raise click.ClickException("没有可用的项目（账号配置可能有误）")

    return {
        "networks": {
            "intnet": {
                "driver": "bridge",
                "internal": True,
                "ipam": {"config": [{"subnet": subnet}]},
            },
            "extnet": {"driver": "bridge"},
        },
        "services": services,
    }


def write_compose(ws: Path) -> Path:
    """Generate docker-compose.yml into workspace. Returns the file path."""
    data = generate_compose(ws)
    dumper = _make_dumper()
    content = (
        "# !! 此文件由 coderfleet apply 自动生成，请勿手动编辑 !!\n\n"
        + yaml.dump(data, Dumper=dumper, allow_unicode=True,
                    sort_keys=False, default_flow_style=False)
    )
    path = ws / "docker-compose.yml"
    path.write_text(content, encoding="utf-8")
    return path
