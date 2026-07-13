"""test_workflow_run_endpoints.py — /api/workflow-runs 端点：不做全目录扫描、不阻塞事件循环。

对应 issue #36（接入线程池）和 #37（按需查任务，避免全量扫描 tasks 目录）。
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from coderfleet.server import main as server_main
from coderfleet.server.models import (
    AccountType,
    NodeExecution,
    Task,
    TaskStatus,
    WorkflowNodeState,
    WorkflowRun,
)
from coderfleet.server.scheduler import Scheduler


def _use_scheduler(monkeypatch: pytest.MonkeyPatch, ws: Path) -> None:
    monkeypatch.setattr(server_main, "scheduler", Scheduler(ws))
    monkeypatch.setattr(server_main, "WORKSPACE_DIR", ws)


def _task(task_id: str) -> Task:
    return Task(
        id=task_id, status=TaskStatus.done, account="alice", type=AccountType.claude,
        prompt="do it", project="/srv/x",
    )


def _run(run_id: str, task_id: str) -> WorkflowRun:
    return WorkflowRun(
        id=run_id, template_id="tpl-1", name="demo", status="succeeded",
        node_executions=[
            NodeExecution(node_id="a", name="build", state=WorkflowNodeState.succeeded, task_id=task_id),
        ],
    )


def test_get_workflow_run_does_not_scan_full_tasks_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path / "workspace"
    ws.mkdir()
    _use_scheduler(monkeypatch, ws)

    _task("t-a").save(server_main.scheduler.tasks_dir)
    _task("t-unrelated").save(server_main.scheduler.tasks_dir)
    _run("wr-1", "t-a").save(server_main.scheduler.workflow_runs_dir)

    def _boom() -> None:
        raise AssertionError("_workflow_run_response 不应再调用 list_tasks() 扫描全部任务")

    monkeypatch.setattr(server_main.scheduler, "list_tasks", _boom)

    result = asyncio.run(server_main.get_workflow_run("wr-1"))

    assert [t.id for t in result.tasks] == ["t-a"]


def test_list_and_get_workflow_run_endpoints_use_threadpool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path / "workspace"
    ws.mkdir()
    _use_scheduler(monkeypatch, ws)

    _task("t-a").save(server_main.scheduler.tasks_dir)
    _run("wr-1", "t-a").save(server_main.scheduler.workflow_runs_dir)

    calls: list[object] = []
    orig = server_main.run_in_threadpool

    async def _tracking(func, *a, **kw):
        calls.append(func)
        return await orig(func, *a, **kw)

    monkeypatch.setattr(server_main, "run_in_threadpool", _tracking)

    list_result = asyncio.run(server_main.list_workflow_runs())
    get_result = asyncio.run(server_main.get_workflow_run("wr-1"))

    assert len(calls) == 2, "list_workflow_runs 和 get_workflow_run 都应通过 run_in_threadpool 卸载阻塞 I/O"
    assert {r.id for r in list_result} == {"wr-1"}
    assert get_result.id == "wr-1"


def test_get_workflow_run_missing_still_raises_404(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi import HTTPException

    ws = tmp_path / "workspace"
    ws.mkdir()
    _use_scheduler(monkeypatch, ws)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(server_main.get_workflow_run("does-not-exist"))

    assert exc_info.value.status_code == 404
