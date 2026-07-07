from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi import HTTPException

from coderfleet.server import main as server_main
from coderfleet.server.main import ProjectCreateRequest, ProjectUpdateRequest
from coderfleet.server.scheduler import Scheduler


def _make_workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "config.conf").write_text(
        "IMAGE_NAME=coderfleet\nIMAGE_TAG=latest\n", encoding="utf-8",
    )
    (ws / "repo").mkdir()
    (ws / "accounts.conf").write_text(
        "NAME=api-claude TYPE=claude AUTH=login\n"
        "NAME=api-codex TYPE=codex AUTH=login\n"
        "NAME=api-claude-2 TYPE=claude AUTH=login\n",
        encoding="utf-8",
    )
    (ws / "projects.conf").write_text("", encoding="utf-8")
    return ws


def _use_scheduler(monkeypatch: pytest.MonkeyPatch, ws: Path) -> None:
    monkeypatch.setattr(server_main, "scheduler", Scheduler(ws))
    monkeypatch.setattr(server_main, "WORKSPACE_DIR", ws)


def test_create_project_accepts_secondary_accounts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = _make_workspace(tmp_path)
    _use_scheduler(monkeypatch, ws)

    req = ProjectCreateRequest(name="repo", account="api-claude", path=str(ws / "repo"), secondary_accounts=["api-codex"])
    resp = asyncio.run(server_main.create_project(req))

    assert resp.secondary_accounts == ["api-codex"]
    assert "SECONDARY_ACCOUNTS=api-codex" in (ws / "projects.conf").read_text(encoding="utf-8")


def test_create_project_rejects_secondary_type_collision(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = _make_workspace(tmp_path)
    _use_scheduler(monkeypatch, ws)

    req = ProjectCreateRequest(name="repo", account="api-claude", path=str(ws / "repo"), secondary_accounts=["api-claude-2"])
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(server_main.create_project(req))

    assert "claude" in exc_info.value.detail
    assert not (ws / "projects.conf").read_text(encoding="utf-8").strip()


def test_update_project_sets_secondary_accounts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = _make_workspace(tmp_path)
    (ws / "projects.conf").write_text(
        f"NAME=repo ACCOUNT=api-claude PATH={ws / 'repo'}\n", encoding="utf-8",
    )
    _use_scheduler(monkeypatch, ws)

    resp = asyncio.run(server_main.update_project("repo", ProjectUpdateRequest(secondary_accounts=["api-codex"])))

    assert resp.secondary_accounts == ["api-codex"]
    assert "SECONDARY_ACCOUNTS=api-codex" in (ws / "projects.conf").read_text(encoding="utf-8")


def test_update_project_rejects_secondary_type_collision(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = _make_workspace(tmp_path)
    (ws / "projects.conf").write_text(
        f"NAME=repo ACCOUNT=api-claude PATH={ws / 'repo'}\n", encoding="utf-8",
    )
    _use_scheduler(monkeypatch, ws)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(server_main.update_project("repo", ProjectUpdateRequest(secondary_accounts=["api-claude-2"])))

    assert "claude" in exc_info.value.detail
    assert "SECONDARY_ACCOUNTS" not in (ws / "projects.conf").read_text(encoding="utf-8")


def test_list_projects_returns_secondary_accounts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from coderfleet.server import docker_mgr

    ws = _make_workspace(tmp_path)
    (ws / "projects.conf").write_text(
        f"NAME=repo ACCOUNT=api-claude PATH={ws / 'repo'} SECONDARY_ACCOUNTS=api-codex\n", encoding="utf-8",
    )
    _use_scheduler(monkeypatch, ws)
    monkeypatch.setattr(docker_mgr, "is_container_running", lambda name: False)

    responses = asyncio.run(server_main.list_projects())

    assert responses[0].secondary_accounts == ["api-codex"]
