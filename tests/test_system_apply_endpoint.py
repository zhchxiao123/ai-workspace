from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from coderfleet.server import main as server_main


def _make_workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "config.conf").write_text(
        "IMAGE_NAME=coderfleet\nIMAGE_TAG=latest\n", encoding="utf-8",
    )
    (ws / "repo").mkdir()
    (ws / "accounts.conf").write_text(
        "NAME=api-claude TYPE=claude AUTH=login\n", encoding="utf-8",
    )
    (ws / "projects.conf").write_text(
        f"NAME=repo ACCOUNT=api-claude PATH={ws / 'repo'}\n", encoding="utf-8",
    )
    return ws


def _install_fake_docker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, call_log: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    docker = bin_dir / "docker"
    docker.write_text(
        "#!/usr/bin/env bash\n"
        f'echo "$@" >> "{call_log}"\n'
        "if [[ \"$1\" == \"compose\" && \"$2\" == \"version\" ]]; then exit 0; fi\n"
        "if [[ \"$1\" == \"compose\" ]]; then exit 0; fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")


async def _drive(full: bool) -> str:
    response = await server_main.system_apply(full=full)
    chunks = []
    async for chunk in response.body_iterator:
        chunks.append(chunk)
    return "".join(chunks)


def test_system_apply_default_is_incremental(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = _make_workspace(tmp_path)
    monkeypatch.setattr(server_main, "WORKSPACE_DIR", ws)
    call_log = tmp_path / "docker-calls.log"
    _install_fake_docker(tmp_path, monkeypatch, call_log)

    output = asyncio.run(_drive(full=False))

    assert "完成" in output
    lines = [line.split() for line in call_log.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert not any("down" in tokens for tokens in lines)
    assert not any("--force-recreate" in tokens for tokens in lines)
    assert any("up" in tokens and "--remove-orphans" in tokens for tokens in lines)


def test_system_apply_full_tears_down_and_force_recreates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = _make_workspace(tmp_path)
    monkeypatch.setattr(server_main, "WORKSPACE_DIR", ws)
    call_log = tmp_path / "docker-calls.log"
    _install_fake_docker(tmp_path, monkeypatch, call_log)

    output = asyncio.run(_drive(full=True))

    assert "完成" in output
    assert "中断" in output
    lines = [line.split() for line in call_log.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert any("down" in tokens and "--remove-orphans" in tokens for tokens in lines)
    assert any("--force-recreate" in tokens for tokens in lines)
