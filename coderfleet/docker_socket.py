"""Docker socket configuration helpers.

The Docker CLI inside CoderFleet containers talks to the host daemon through a
mounted Unix socket. Docker Desktop, Colima, Linux Docker, and rootless Docker
all expose different socket paths, so compose generation goes through this
small resolver instead of hard-coding /var/run/docker.sock.
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path


DEFAULT_CONTAINER_SOCKET = "/var/run/docker.sock"


@dataclass(frozen=True)
class DockerSocketMount:
    host_path: str
    container_path: str = DEFAULT_CONTAINER_SOCKET

    @property
    def env(self) -> str:
        return f"unix://{self.container_path}"

    @property
    def volume(self) -> str:
        return f"{self.host_path}:{self.container_path}"


def _path_from_unix_endpoint(value: str) -> str:
    value = value.strip()
    if value.startswith("unix://"):
        value = value[len("unix://"):]
    if not value or "://" in value:
        return ""
    return str(Path(value).expanduser())


def _docker_context_host() -> str:
    try:
        result = subprocess.run(
            ["docker", "context", "inspect", "--format", '{{ (index .Endpoints "docker").Host }}'],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def _candidate_socket_paths(docker_host: str, context_host: str) -> list[str]:
    uid = os.getuid() if hasattr(os, "getuid") else 0
    candidates = [
        _path_from_unix_endpoint(docker_host),
        _path_from_unix_endpoint(context_host),
        "/var/run/docker.sock",
        str(Path.home() / ".docker" / "run" / "docker.sock"),
        str(Path.home() / ".colima" / "default" / "docker.sock"),
        f"/run/user/{uid}/docker.sock",
        f"/run/user/{uid}/podman/podman.sock",
    ]
    seen: set[str] = set()
    return [p for p in candidates if p and not (p in seen or seen.add(p))]


def resolve_docker_socket(
    cfg: dict[str, str],
    *,
    docker_host: str | None = None,
    context_host: str | None = None,
) -> DockerSocketMount | None:
    """Resolve config.conf Docker socket settings to a bind mount.

    DOCKER_SOCKET supports:
      - off / empty: do not mount Docker
      - auto: detect the current Unix socket from DOCKER_HOST, docker context, or common paths
      - /path/to/docker.sock or unix:///path/to/docker.sock: use an explicit socket
    """
    configured = cfg.get("DOCKER_SOCKET", "").strip()
    if not configured or configured.lower() in {"off", "false", "0", "no"}:
        return None

    container_path = cfg.get("DOCKER_SOCKET_TARGET", DEFAULT_CONTAINER_SOCKET).strip()
    if not container_path:
        container_path = DEFAULT_CONTAINER_SOCKET

    if configured.lower() == "auto":
        env_host = os.environ.get("DOCKER_HOST", "") if docker_host is None else docker_host
        ctx_host = _docker_context_host() if context_host is None else context_host
        for path in _candidate_socket_paths(env_host, ctx_host):
            if Path(path).exists():
                return DockerSocketMount(host_path=path, container_path=container_path)
        return None

    host_path = _path_from_unix_endpoint(configured)
    if not host_path:
        return None
    return DockerSocketMount(host_path=host_path, container_path=container_path)


def docker_socket_config_for_project(
    global_cfg: dict[str, str],
    project_record: dict[str, str],
) -> dict[str, str]:
    """Return Docker socket config with project-level values overriding globals."""
    cfg = dict(global_cfg)
    if "DOCKER_SOCKET" in project_record:
        cfg["DOCKER_SOCKET"] = project_record.get("DOCKER_SOCKET", "")
    if "DOCKER_SOCKET_TARGET" in project_record:
        cfg["DOCKER_SOCKET_TARGET"] = project_record.get("DOCKER_SOCKET_TARGET", "")
    return cfg
