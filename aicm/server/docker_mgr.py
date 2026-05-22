"""
docker_mgr.py — Docker 操作封装
"""
from __future__ import annotations

import shutil
import subprocess


def _dc_cmd() -> str:
    result = subprocess.run(
        ["docker", "compose", "version"],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        return "docker compose"
    if shutil.which("docker-compose"):
        return "docker-compose"
    raise RuntimeError("找不到 docker compose 或 docker-compose")


_DC: str | None = None


def _get_dc() -> str:
    global _DC
    if _DC is None:
        _DC = _dc_cmd()
    return _DC


def is_container_running(container_name: str) -> bool:
    result = subprocess.run(
        ["docker", "inspect", container_name, "--format", "{{.State.Running}}"],
        capture_output=True, text=True,
    )
    return result.stdout.strip() == "true"


def exec_sync(container_name: str, cmd: str) -> tuple[int, str, str]:
    result = subprocess.run(
        ["docker", "exec", container_name, "bash", "-c", cmd],
        capture_output=True, text=True,
    )
    return result.returncode, result.stdout, result.stderr
