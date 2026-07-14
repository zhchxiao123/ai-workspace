from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

import coderfleet.docker_ops as docker_ops
import coderfleet.compose as compose
from coderfleet.server import main as server_main
from coderfleet.server.scheduler import Scheduler


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


def _use_scheduler(monkeypatch: pytest.MonkeyPatch, ws: Path) -> None:
    monkeypatch.setattr(server_main, "scheduler", Scheduler(ws))
    monkeypatch.setattr(server_main, "WORKSPACE_DIR", ws)


class _FakeResult:
    def __init__(self, returncode: int) -> None:
        self.returncode = returncode


def test_start_project_container_regenerates_compose_and_starts_only_that_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = _make_workspace(tmp_path)
    _use_scheduler(monkeypatch, ws)

    calls: list[tuple] = []

    def fake_write_compose(workspace: Path) -> None:
        calls.append(("write_compose", workspace))

    def fake_start_services(workspace: Path, services: list[str]) -> _FakeResult:
        calls.append(("start", workspace, services))
        return _FakeResult(0)

    monkeypatch.setattr(compose, "write_compose", fake_write_compose)
    monkeypatch.setattr(docker_ops, "start_services", fake_start_services)

    result = asyncio.run(server_main.start_project_container("repo"))

    assert result == {"ok": True}
    assert ("write_compose", ws) in calls
    assert ("start", ws, ["claude-project-repo"]) in calls


def test_start_project_container_unknown_project_returns_404(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = _make_workspace(tmp_path)
    _use_scheduler(monkeypatch, ws)

    with pytest.raises(Exception) as exc_info:
        asyncio.run(server_main.start_project_container("does-not-exist"))

    assert getattr(exc_info.value, "status_code", None) == 404


def test_start_project_container_reports_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = _make_workspace(tmp_path)
    _use_scheduler(monkeypatch, ws)

    monkeypatch.setattr(compose, "write_compose", lambda workspace: None)
    monkeypatch.setattr(docker_ops, "start_services", lambda workspace, services: _FakeResult(1))

    with pytest.raises(Exception) as exc_info:
        asyncio.run(server_main.start_project_container("repo"))

    assert getattr(exc_info.value, "status_code", None) == 500


def test_stop_project_container_uses_non_destructive_stop_scoped_to_one_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = _make_workspace(tmp_path)
    _use_scheduler(monkeypatch, ws)

    calls: list[tuple] = []

    def fake_stop_services(workspace: Path, services: list[str]) -> _FakeResult:
        calls.append(("stop", workspace, services))
        return _FakeResult(0)

    monkeypatch.setattr(docker_ops, "stop_services", fake_stop_services)

    result = asyncio.run(server_main.stop_project_container("repo"))

    assert result == {"ok": True}
    assert ("stop", ws, ["claude-project-repo"]) in calls


def test_get_project_container_status_reflects_running_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = _make_workspace(tmp_path)
    _use_scheduler(monkeypatch, ws)

    from coderfleet.server import docker_mgr

    monkeypatch.setattr(docker_mgr, "is_container_running", lambda name: name == "claude-repo")

    status = asyncio.run(server_main.get_project_container_status("repo"))

    assert status == {"running": True}


def test_list_projects_exposes_container_running_per_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = _make_workspace(tmp_path)
    _use_scheduler(monkeypatch, ws)

    from coderfleet.server import docker_mgr

    monkeypatch.setattr(docker_mgr, "is_container_running", lambda name: name == "claude-repo")

    projects = asyncio.run(server_main.list_projects())

    assert len(projects) == 1
    assert projects[0].name == "repo"
    assert projects[0].container_running is True


def test_list_projects_runs_in_threadpool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """issue #41：/api/projects 对每个项目都做一次同步 docker inspect 子进程调用，
    不该直接阻塞事件循环，得丢进线程池执行。"""
    ws = _make_workspace(tmp_path)
    _use_scheduler(monkeypatch, ws)

    from coderfleet.server import docker_mgr

    monkeypatch.setattr(docker_mgr, "is_container_running", lambda name: True)

    calls: list[object] = []
    orig = server_main.run_in_threadpool

    async def _tracking(func, *a, **kw):
        calls.append(func)
        return await orig(func, *a, **kw)

    monkeypatch.setattr(server_main, "run_in_threadpool", _tracking)

    projects = asyncio.run(server_main.list_projects())

    assert calls, "list_projects 应通过 run_in_threadpool 卸载阻塞 I/O"
    assert len(projects) == 1


def test_list_accounts_runs_in_threadpool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """issue #41：/api/accounts 对账号下每个项目都做一次同步 docker inspect 子进程调用，
    不该直接阻塞事件循环，得丢进线程池执行。"""
    ws = _make_workspace(tmp_path)
    _use_scheduler(monkeypatch, ws)

    from coderfleet.server import docker_mgr

    monkeypatch.setattr(docker_mgr, "is_container_running", lambda name: True)

    calls: list[object] = []
    orig = server_main.run_in_threadpool

    async def _tracking(func, *a, **kw):
        calls.append(func)
        return await orig(func, *a, **kw)

    monkeypatch.setattr(server_main, "run_in_threadpool", _tracking)

    accounts = asyncio.run(server_main.list_accounts())

    assert calls, "list_accounts 应通过 run_in_threadpool 卸载阻塞 I/O"
    assert len(accounts) == 1
    assert accounts[0].name == "api-claude"
