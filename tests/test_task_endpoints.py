"""test_task_endpoints.py — /api/tasks 端点的过滤行为。"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from coderfleet.server import main as server_main
from coderfleet.server.models import AccountType, Task, TaskStatus
from coderfleet.server.scheduler import Scheduler


def _use_scheduler(monkeypatch: pytest.MonkeyPatch, ws: Path) -> None:
    monkeypatch.setattr(server_main, "scheduler", Scheduler(ws))
    monkeypatch.setattr(server_main, "WORKSPACE_DIR", ws)


def _task(id: str, conversation_id: str = "") -> Task:
    return Task(
        id=id, status=TaskStatus.done, account="alice", type=AccountType.claude,
        prompt=f"prompt-{id}", project="/srv/x", project_name="web",
        conversation_id=conversation_id,
    )


def test_list_tasks_filters_by_conversation_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = tmp_path / "workspace"
    ws.mkdir()
    _use_scheduler(monkeypatch, ws)

    _task("t1", conversation_id="conv-a").save(server_main.scheduler.tasks_dir)
    _task("t2", conversation_id="conv-b").save(server_main.scheduler.tasks_dir)
    _task("t3", conversation_id="conv-a").save(server_main.scheduler.tasks_dir)

    result = asyncio.run(server_main.list_tasks(
        status=None, account=None, conversation_id="conv-a", limit=50, include_archived=False,
    ))

    assert {r.id for r in result} == {"t1", "t3"}


def test_tasks_heartbeat_returns_slim_status_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """心跳端点只用于探测状态变化，payload 里不该带 prompt/images 这类大字段。"""
    ws = tmp_path / "workspace"
    ws.mkdir()
    _use_scheduler(monkeypatch, ws)

    _task("t1", conversation_id="conv-a").save(server_main.scheduler.tasks_dir)

    result = asyncio.run(server_main.tasks_heartbeat())

    assert {r.id for r in result} == {"t1"}
    entry = next(r for r in result if r.id == "t1")
    assert entry.status == TaskStatus.done
    assert entry.conversation_id == "conv-a"
    assert "prompt" not in entry.model_dump()


def test_list_tasks_without_conversation_id_returns_everything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path / "workspace"
    ws.mkdir()
    _use_scheduler(monkeypatch, ws)

    _task("t1", conversation_id="conv-a").save(server_main.scheduler.tasks_dir)
    _task("t2", conversation_id="conv-b").save(server_main.scheduler.tasks_dir)

    result = asyncio.run(server_main.list_tasks(
        status=None, account=None, conversation_id=None, limit=50, include_archived=False,
    ))

    assert {r.id for r in result} == {"t1", "t2"}


def test_manual_pipeline_creation_endpoints_removed() -> None:
    """v1 手动创建流水线/加任务端点已下线（见 #17）。"""
    from coderfleet.server import main as m
    post_paths = {
        r.path for r in m.app.routes
        if "POST" in getattr(r, "methods", set())
    }
    assert "/api/pipelines" not in post_paths
    assert "/api/pipelines/{pipeline_id}/tasks" not in post_paths
    # 只读/删除/恢复端点保留以兼容历史数据
    all_paths = {r.path for r in m.app.routes}
    assert "/api/pipelines" in all_paths  # GET 仍在
