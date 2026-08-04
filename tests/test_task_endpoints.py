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


def test_list_tasks_response_carries_git_branch_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path / "workspace"
    ws.mkdir()
    _use_scheduler(monkeypatch, ws)

    task = _task("t1")
    task.git_branch = "feature/x"
    task.git_worktree = True
    task.save(server_main.scheduler.tasks_dir)

    result = asyncio.run(server_main.list_tasks(
        status=None, account=None, conversation_id=None, limit=50, include_archived=False,
    ))

    assert result[0].git_branch == "feature/x"
    assert result[0].git_worktree is True


def test_list_tasks_response_defaults_git_branch_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path / "workspace"
    ws.mkdir()
    _use_scheduler(monkeypatch, ws)

    _task("t1").save(server_main.scheduler.tasks_dir)

    result = asyncio.run(server_main.list_tasks(
        status=None, account=None, conversation_id=None, limit=50, include_archived=False,
    ))

    assert result[0].git_branch == ""
    assert result[0].git_worktree is False


def test_list_tasks_response_carries_git_diff_stat_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path / "workspace"
    ws.mkdir()
    _use_scheduler(monkeypatch, ws)

    task = _task("t1")
    task.git_branch = "feature/x"
    task.git_diff_added = 42
    task.git_diff_removed = 7
    task.save(server_main.scheduler.tasks_dir)

    result = asyncio.run(server_main.list_tasks(
        status=None, account=None, conversation_id=None, limit=50, include_archived=False,
    ))

    assert result[0].git_diff_added == 42
    assert result[0].git_diff_removed == 7


def test_list_tasks_response_defaults_git_diff_stat_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path / "workspace"
    ws.mkdir()
    _use_scheduler(monkeypatch, ws)

    _task("t1").save(server_main.scheduler.tasks_dir)

    result = asyncio.run(server_main.list_tasks(
        status=None, account=None, conversation_id=None, limit=50, include_archived=False,
    ))

    assert result[0].git_diff_added == 0
    assert result[0].git_diff_removed == 0


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


def test_answer_task_intervention_resolves_pending_waiter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path / "workspace"
    ws.mkdir()
    _use_scheduler(monkeypatch, ws)
    _task("ask-1").save(server_main.scheduler.tasks_dir)

    async def _run():
        waiter = asyncio.create_task(
            server_main.scheduler.wait_for_intervention_answer(
                "ask-1", [{"question": "ok?"}], timeout_seconds=5,
            )
        )
        await asyncio.sleep(0)
        pending = server_main.scheduler.get_task("ask-1").pending_intervention
        assert pending is not None

        result = await server_main.answer_task_intervention(
            "ask-1",
            server_main.TaskAnswerRequest(
                tool_call_id=pending.tool_call_id, token=pending.token, answers={"ok?": "yes"},
            ),
        )
        answers = await waiter
        return result, answers

    result, answers = asyncio.run(_run())
    assert result == {"ok": True}
    assert answers == {"ok?": "yes"}


def test_answer_task_intervention_404_when_no_pending_question(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi import HTTPException

    ws = tmp_path / "workspace"
    ws.mkdir()
    _use_scheduler(monkeypatch, ws)
    _task("ask-2").save(server_main.scheduler.tasks_dir)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(server_main.answer_task_intervention(
            "ask-2",
            server_main.TaskAnswerRequest(tool_call_id="x", token="y", answers={}),
        ))
    assert exc_info.value.status_code == 404


def test_answer_task_intervention_409_on_wrong_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi import HTTPException

    ws = tmp_path / "workspace"
    ws.mkdir()
    _use_scheduler(monkeypatch, ws)
    _task("ask-3").save(server_main.scheduler.tasks_dir)

    async def _run():
        waiter = asyncio.create_task(
            server_main.scheduler.wait_for_intervention_answer(
                "ask-3", [{"question": "ok?"}], timeout_seconds=5,
            )
        )
        await asyncio.sleep(0)
        pending = server_main.scheduler.get_task("ask-3").pending_intervention
        assert pending is not None

        with pytest.raises(HTTPException) as exc_info:
            await server_main.answer_task_intervention(
                "ask-3",
                server_main.TaskAnswerRequest(
                    tool_call_id=pending.tool_call_id, token="wrong-token", answers={},
                ),
            )
        assert exc_info.value.status_code == 409

        # Clean up the still-open waiter so the test doesn't leak a pending task.
        server_main.scheduler.resolve_intervention(
            "ask-3", pending.tool_call_id, pending.token, {"ok?": "yes"}
        )
        await waiter

    asyncio.run(_run())


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
