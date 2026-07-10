from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from coderfleet.server import scheduler as scheduler_mod
from coderfleet.server.scheduler import Scheduler
from coderfleet.server.models import AccountType, Task, TaskStatus


def _make_scheduler(tmp_path: Path, account: str = "acc1", project: str = "proj1") -> Scheduler:
    (tmp_path / "accounts.conf").write_text(f"NAME={account} TYPE=claude AUTH=login\n")
    (tmp_path / "projects.conf").write_text(f"NAME={project} ACCOUNT={account} PATH=/tmp/{project}\n")
    (tmp_path / "accounts" / account).mkdir(parents=True)
    (tmp_path / "tasks").mkdir(parents=True, exist_ok=True)
    return Scheduler(tmp_path)


def _save_task(sched: Scheduler, task_id: str, account: str, status: TaskStatus) -> None:
    t = Task(id=task_id, status=status, account=account, type=AccountType.claude, prompt="x",
              project="/tmp/proj1", project_name="proj1")
    t.save(sched.tasks_dir)


def test_delay_idle_with_no_task_history(tmp_path: Path) -> None:
    sched = _make_scheduler(tmp_path)
    busy = sched.get_busy_accounts()
    assert sched._usage_adaptive_delay("acc1", busy) == Scheduler._USAGE_DELAY_IDLE


def test_delay_busy_when_task_running(tmp_path: Path) -> None:
    sched = _make_scheduler(tmp_path)
    _save_task(sched, "t1", "acc1", TaskStatus.running)
    busy = sched.get_busy_accounts()
    assert sched._usage_adaptive_delay("acc1", busy) == Scheduler._USAGE_DELAY_BUSY


def test_delay_idle_when_task_already_finished(tmp_path: Path) -> None:
    sched = _make_scheduler(tmp_path)
    _save_task(sched, "t1", "acc1", TaskStatus.done)
    busy = sched.get_busy_accounts()
    assert sched._usage_adaptive_delay("acc1", busy) == Scheduler._USAGE_DELAY_IDLE


def test_poll_skips_account_not_yet_due(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(scheduler_mod.docker_mgr, "is_container_running", lambda _name: False)
    sched = _make_scheduler(tmp_path)

    calls: list[str] = []
    original = sched.refresh_account_usage

    async def spy(name: str):
        calls.append(name)
        return await original(name)

    monkeypatch.setattr(sched, "refresh_account_usage", spy)

    asyncio.run(sched._poll_due_account_usage())
    assert calls == ["acc1"]

    # No running task -> 1h idle delay, so an immediate second tick must skip.
    asyncio.run(sched._poll_due_account_usage())
    assert calls == ["acc1"]


def test_poll_reprobes_busy_account_sooner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(scheduler_mod.docker_mgr, "is_container_running", lambda _name: False)
    sched = _make_scheduler(tmp_path)
    _save_task(sched, "t1", "acc1", TaskStatus.running)

    calls: list[str] = []
    original = sched.refresh_account_usage

    async def spy(name: str):
        calls.append(name)
        return await original(name)

    monkeypatch.setattr(sched, "refresh_account_usage", spy)

    asyncio.run(sched._poll_due_account_usage())
    assert calls == ["acc1"]

    # Backdate the cached fetch beyond the busy-tier delay (5 min) but still well within
    # the idle delay (1h), to prove the *busy* tier governs the re-check cadence.
    cached = sched._usage_cache["acc1"]
    cached.fetched_at = (datetime.now() - timedelta(seconds=Scheduler._USAGE_DELAY_BUSY + 1)).isoformat(timespec="seconds")

    asyncio.run(sched._poll_due_account_usage())
    assert calls == ["acc1", "acc1"]
