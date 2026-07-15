"""test_task_log_endpoints.py — /api/tasks/{id}/logs 与 /output 不应阻塞事件循环。

回归背景：这两个接口曾经在 `async def` 里直接同步 `Path.read_text()`，任务日志文件可达数 MB，
同步读取会独占 coderfleet 单进程单事件循环，挤占同一时刻的其它并发请求（issue #44）。
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from coderfleet.server import main as server_main
from coderfleet.server.models import AccountType, Task, TaskStatus
from coderfleet.server.scheduler import Scheduler

BLOCKING_READ_SECONDS = 0.3


def _use_scheduler(monkeypatch: pytest.MonkeyPatch, ws: Path) -> None:
    monkeypatch.setattr(server_main, "scheduler", Scheduler(ws))
    monkeypatch.setattr(server_main, "WORKSPACE_DIR", ws)


def _make_task_with_log(ws: Path, task_id: str) -> Path:
    task = Task(
        id=task_id, status=TaskStatus.done, account="alice", type=AccountType.claude,
        prompt="hello", project="/srv/x", project_name="web",
    )
    task.save(server_main.scheduler.tasks_dir)
    log_path = server_main.scheduler.get_log_path(task_id)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("=== CoderFleet Task Log ===\nhello world\n", encoding="utf-8")
    return log_path


def _simulate_slow_disk(monkeypatch: pytest.MonkeyPatch, target: Path) -> None:
    """把 `target` 这一个文件的 Path.read_text 换成"读之前先阻塞 sleep"，模拟慢磁盘/大文件读取。
    只针对目标文件打补丁，避免连带拖慢 scheduler.get_task() 内部读取任务 JSON 等无关调用，
    污染"是否阻塞事件循环"这个信号。"""
    real_read_text = Path.read_text

    def slow_read_text(self, *args, **kwargs):
        if self == target:
            time.sleep(BLOCKING_READ_SECONDS)
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", slow_read_text)


async def _run_with_concurrent_marker(coro) -> list[float]:
    """并发跑一个高频 marker 协程；如果 coro 同步阻塞了事件循环，marker 的第一次 tick
    会被推迟到 coro 的阻塞操作结束之后，而不是按预期的高频间隔触发。"""
    tick_times: list[float] = []

    async def marker():
        for _ in range(5):
            tick_times.append(time.perf_counter())
            await asyncio.sleep(0.02)

    await asyncio.gather(coro, marker())
    return tick_times


def test_get_logs_does_not_block_event_loop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = tmp_path / "workspace"
    ws.mkdir()
    _use_scheduler(monkeypatch, ws)
    log_path = _make_task_with_log(ws, "big-task")
    _simulate_slow_disk(monkeypatch, log_path)

    t0 = time.perf_counter()
    tick_times = asyncio.run(_run_with_concurrent_marker(server_main.get_logs("big-task")))

    first_tick_delay = tick_times[0] - t0
    assert first_tick_delay < BLOCKING_READ_SECONDS / 2, (
        f"marker 的第一次 tick 延迟了 {first_tick_delay:.3f}s，"
        f"说明 get_logs 同步阻塞了事件循环（模拟的慢读取耗时 {BLOCKING_READ_SECONDS}s）"
    )


def test_get_task_output_does_not_block_event_loop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = tmp_path / "workspace"
    ws.mkdir()
    _use_scheduler(monkeypatch, ws)
    log_path = _make_task_with_log(ws, "big-task")
    _simulate_slow_disk(monkeypatch, log_path)

    t0 = time.perf_counter()
    tick_times = asyncio.run(_run_with_concurrent_marker(server_main.get_task_output("big-task")))

    first_tick_delay = tick_times[0] - t0
    assert first_tick_delay < BLOCKING_READ_SECONDS / 2, (
        f"marker 的第一次 tick 延迟了 {first_tick_delay:.3f}s，"
        f"说明 get_task_output 同步阻塞了事件循环（模拟的慢读取耗时 {BLOCKING_READ_SECONDS}s）"
    )


def test_get_logs_returns_log_content(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = tmp_path / "workspace"
    ws.mkdir()
    _use_scheduler(monkeypatch, ws)
    _make_task_with_log(ws, "t1")

    result = asyncio.run(server_main.get_logs("t1"))
    assert "hello world" in result


def test_get_logs_missing_file_raises_404(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = tmp_path / "workspace"
    ws.mkdir()
    _use_scheduler(monkeypatch, ws)

    with pytest.raises(Exception) as exc_info:
        asyncio.run(server_main.get_logs("does-not-exist"))
    assert getattr(exc_info.value, "status_code", None) == 404
