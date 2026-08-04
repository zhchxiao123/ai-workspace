"""test_local_sandbox_gate.py — scenario 5: local 账号的 auto 任务沙箱确认门禁。

--dangerously-skip-permissions（Claude）/ --dangerously-bypass-approvals-and-sandbox（Codex）
两者官方文档都假设有外部（容器）沙箱兜底；local runtime 没有容器，必须账号自己显式确认
已经开启 CLI 自带的 OS 级沙箱，门禁才放行 auto 任务。
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from coderfleet.account_type_registry import _build_claude, _build_codex
from coderfleet.server.models import Account, AccountRuntime, AccountType, TaskStatus
from coderfleet.server.models import Task
from coderfleet.server.runtime import FakeProcess, FakeRuntime
from coderfleet.server.scheduler import Scheduler


# ── _build_codex：local_sandboxed 换旗标 ────────────────────────────────
def test_build_codex_auto_uses_danger_full_access_by_default() -> None:
    cmd = _build_codex("hi", True, "t1", "m1", "t1", "", [], "")
    assert "--sandbox danger-full-access" in cmd
    assert "workspace-write" not in cmd


def test_build_codex_local_sandboxed_downgrades_new_session_to_workspace_write() -> None:
    cmd = _build_codex("hi", True, "t1", "m1", "t1", "", [], "", local_sandboxed=True)
    assert "--sandbox workspace-write" in cmd
    assert "danger-full-access" not in cmd


def test_build_codex_auto_resume_uses_bypass_flag_by_default() -> None:
    cmd = _build_codex("hi", True, "t1", "m1", "t1", "sess-1", [], "")
    assert "--dangerously-bypass-approvals-and-sandbox" in cmd


def test_build_codex_local_sandboxed_resume_uses_sandbox_flag_not_bypass() -> None:
    cmd = _build_codex("hi", True, "t1", "m1", "t1", "sess-1", [], "", local_sandboxed=True)
    assert "--dangerously-bypass-approvals-and-sandbox" not in cmd
    assert "--sandbox workspace-write" in cmd


def test_build_codex_non_auto_ignores_local_sandboxed() -> None:
    """non-auto 本来就不用 danger 旗标，local_sandboxed 不应该改变已有的 workspace-write 行为。"""
    cmd = _build_codex("hi", False, "t1", "m1", "t1", "", [], "", local_sandboxed=True)
    assert "--sandbox workspace-write" in cmd


# ── _build_claude：接受但不使用 local_sandboxed（旗标本身不需要变） ─────
def test_build_claude_accepts_local_sandboxed_without_changing_flags() -> None:
    without = _build_claude("hi", True, "t1", "m1", "t1", "", [], "")
    with_flag = _build_claude("hi", True, "t1", "m1", "t1", "", [], "", local_sandboxed=True)
    assert without == with_flag
    assert "--dangerously-skip-permissions" in with_flag


# ── Scheduler.check_local_sandbox_gate：纯逻辑 ──────────────────────────
def test_gate_blocks_local_auto_without_sandbox_confirmed(tmp_path: Path) -> None:
    sched = Scheduler(tmp_path)
    acc = Account(name="a", type=AccountType.claude, runtime=AccountRuntime.local, sandbox_confirmed=False)
    msg = sched.check_local_sandbox_gate(acc, auto=True)
    assert msg  # 非空 = 拒绝
    assert "sandbox_confirmed" not in msg  # 面向用户的错误信息不应该暴露内部字段名
    assert "auto" in msg or "沙箱" in msg


def test_gate_allows_local_auto_with_sandbox_confirmed(tmp_path: Path) -> None:
    sched = Scheduler(tmp_path)
    acc = Account(name="a", type=AccountType.claude, runtime=AccountRuntime.local, sandbox_confirmed=True)
    assert sched.check_local_sandbox_gate(acc, auto=True) == ""


def test_gate_allows_local_non_auto_without_sandbox_confirmed(tmp_path: Path) -> None:
    """非 auto 任务本来就不用 dangerous 旗标，门禁不应该拦它。"""
    sched = Scheduler(tmp_path)
    acc = Account(name="a", type=AccountType.claude, runtime=AccountRuntime.local, sandbox_confirmed=False)
    assert sched.check_local_sandbox_gate(acc, auto=False) == ""


def test_gate_allows_container_auto_without_sandbox_confirmed(tmp_path: Path) -> None:
    """容器场景本来就有 docker 边界兜底，sandbox_confirmed 对它无意义，门禁不拦。"""
    sched = Scheduler(tmp_path)
    acc = Account(name="a", type=AccountType.claude, runtime=AccountRuntime.container, sandbox_confirmed=False)
    assert sched.check_local_sandbox_gate(acc, auto=True) == ""


# ── 端到端：Scheduler._run_ephemeral_task 真的会被门禁挡住/放行 ─────────
def test_ephemeral_task_local_auto_without_confirmation_fails_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    local_rt = FakeRuntime()  # 不应该被调用 —— 门禁应该在拼命令之前就拦下
    sched = Scheduler(tmp_path, local_runtime=local_rt)
    task = Task(
        id="task-gate-1", status=TaskStatus.running, account="alice",
        type=AccountType.codex, prompt="hi", project="",
    )
    task.save(sched.tasks_dir)
    acc = Account(name="alice", type=AccountType.codex, runtime=AccountRuntime.local, sandbox_confirmed=False)
    log_path = sched.get_log_path(task.id)
    sched._write_log_header(log_path, task, acc)

    monkeypatch.setattr(sched, "_get_account_image", lambda _acc, _project=None: "unused")

    asyncio.run(sched._run_ephemeral_task(task, acc, log_path, auto=True))

    assert len(local_rt.runs) == 0
    saved = sched.get_task(task.id)
    assert saved.status == TaskStatus.failed
    assert "沙箱" in log_path.read_text() or "sandbox" in log_path.read_text().lower()


def test_ephemeral_task_local_auto_with_confirmation_runs_and_uses_workspace_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    local_rt = FakeRuntime().queue(FakeProcess(returncode=0))
    sched = Scheduler(tmp_path, local_runtime=local_rt)
    task = Task(
        id="task-gate-2", status=TaskStatus.running, account="alice",
        type=AccountType.codex, prompt="hi", project="",
    )
    task.save(sched.tasks_dir)
    acc = Account(name="alice", type=AccountType.codex, runtime=AccountRuntime.local, sandbox_confirmed=True)
    log_path = sched.get_log_path(task.id)
    sched._write_log_header(log_path, task, acc)

    monkeypatch.setattr(sched, "_get_account_image", lambda _acc, _project=None: "unused")

    asyncio.run(sched._run_ephemeral_task(task, acc, log_path, auto=True))

    assert len(local_rt.runs) == 1
    spec = local_rt.runs[0]
    wrapped_cmd = spec.command[2]
    assert "--sandbox workspace-write" in wrapped_cmd
    assert "danger-full-access" not in wrapped_cmd
    assert sched.get_task(task.id).status == TaskStatus.done
