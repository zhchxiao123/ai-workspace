"""test_runtime.py — 容器运行时 seam 的单元测试。"""
from __future__ import annotations

import asyncio

from pathlib import Path

import pytest

from coderfleet.server.models import Account, AccountType, Task, TaskStatus
from coderfleet.server.runtime import (
    ContainerSpec,
    DockerRuntime,
    FakeProcess,
    FakeRuntime,
)
from coderfleet.server.scheduler import Scheduler


# ── DockerRuntime argv 拼装（纯函数）────────────────────────────────────
def test_build_run_argv_oneshot() -> None:
    spec = ContainerSpec(
        image="coderfleet:test",
        command=["bash", "-c", "echo hi"],
        env={"FOO": "bar"},
        mounts=[("/host/ws", "/workspace")],
        name="coderfleet-ephemeral-t1",
        remove=True,
    )
    argv = DockerRuntime.build_run_argv(spec)
    assert argv[:2] == ["docker", "run"]
    assert "--pull" in argv and "never" in argv
    assert "--rm" in argv
    assert "-d" not in argv
    assert ["-e", "FOO=bar"] == argv[argv.index("-e"):argv.index("-e") + 2]
    assert ["-v", "/host/ws:/workspace"] == argv[argv.index("-v"):argv.index("-v") + 2]
    # image 后紧跟容器 argv
    idx = argv.index("coderfleet:test")
    assert argv[idx + 1:] == ["bash", "-c", "echo hi"]


def test_build_run_argv_detached_session_has_no_rm() -> None:
    spec = ContainerSpec(
        image="coderfleet:test",
        command=["tail", "-f", "/dev/null"],
        name="coderfleet-eph-session-c1",
        detached=True,
        remove=False,
        network="coderfleet-net",
    )
    argv = DockerRuntime.build_run_argv(spec)
    assert "-d" in argv
    assert "--rm" not in argv
    assert ["--network", "coderfleet-net"] == argv[argv.index("--network"):argv.index("--network") + 2]


def test_build_exec_argv_with_and_without_workdir() -> None:
    with_wd = DockerRuntime.build_exec_argv(
        "c1", ["bash", "-c", "run"], env={"K": "V"}, workdir="/workspace"
    )
    assert with_wd[:2] == ["docker", "exec"]
    assert ["-w", "/workspace"] == with_wd[with_wd.index("-w"):with_wd.index("-w") + 2]
    assert with_wd[with_wd.index("c1"):] == ["c1", "bash", "-c", "run"]

    no_wd = DockerRuntime.build_exec_argv("c1", ["bash", "-c", "run"])
    assert "-w" not in no_wd
    assert no_wd[no_wd.index("c1"):] == ["c1", "bash", "-c", "run"]


# ── FakeRuntime 记录与脚本化 ────────────────────────────────────────────
def test_fake_runtime_records_and_scripts() -> None:
    rt = FakeRuntime().queue(
        FakeProcess(stdout_chunks=[b"container-id\n"], returncode=0),
        FakeProcess(stdout_chunks=[b"log line"], returncode=0),
    )

    async def drive() -> None:
        p0 = await rt.run(ContainerSpec(image="img", command=["tail", "-f", "/dev/null"], name="s1", detached=True))
        assert (await p0.wait()) == 0
        p1 = await rt.exec("s1", ["bash", "-c", "task"], env={"A": "1"}, workdir="/workspace")
        out = await p1.stdout.read()
        assert out == b"log line"

    asyncio.run(drive())

    assert len(rt.runs) == 1
    assert rt.runs[0].detached is True and rt.runs[0].name == "s1"
    assert rt.execs == [("s1", ["bash", "-c", "task"], {"A": "1"}, "/workspace")]


def test_fake_runtime_is_running_and_remove() -> None:
    rt = FakeRuntime()
    rt.running.add("c1")
    assert rt.is_running("c1") is True
    assert rt.is_running("c2") is False
    asyncio.run(rt.remove("c1"))
    assert rt.removed == ["c1"]
    assert rt.is_running("c1") is False


# ── _run（持久路径）现在跑真的：不再整体 stub，只注入 FakeRuntime ────────
def _mk_task_acc(sched: Scheduler) -> tuple[Task, Account, Path]:
    task = Task(
        id="task-run-1",
        status=TaskStatus.running,
        account="alice",
        type=AccountType.claude,
        prompt="fix tests",
        project="",
    )
    acc = Account(name="alice", type=AccountType.claude)
    log_path = sched.get_log_path(task.id)
    sched._write_log_header(log_path, task, acc)
    return task, acc, log_path


def test_run_persistent_spawns_via_runtime_then_streams(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rt = FakeRuntime().queue(FakeProcess(returncode=0))
    sched = Scheduler(tmp_path, runtime=rt)
    task, acc, log_path = _mk_task_acc(sched)

    streamed: list[str] = []

    async def fake_stream(t, a, lp, host_log, host_exit, conv):
        streamed.append(t.id)
        t.update_status(TaskStatus.done, sched.tasks_dir)

    monkeypatch.setattr(sched, "_stream_container_log", fake_stream)
    monkeypatch.setattr(sched, "_get_project_root", lambda _t: tmp_path)

    import asyncio as _a
    _a.run(sched._run(task, acc, log_path, auto=False, container_name="coderfleet-alice"))

    # exec 经过 seam：容器名 + bash -c 载荷
    assert len(rt.execs) == 1
    container, command, env, workdir = rt.execs[0]
    assert container == "coderfleet-alice"
    assert command[:2] == ["bash", "-c"]
    # 真正走到了下游流式跟踪，并置为 done
    assert streamed == [task.id]
    assert sched.get_task(task.id).status == TaskStatus.done


def test_run_persistent_nonzero_exit_marks_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rt = FakeRuntime().queue(FakeProcess(stdout_chunks=[b"boom"], returncode=1))
    sched = Scheduler(tmp_path, runtime=rt)
    task, acc, log_path = _mk_task_acc(sched)

    async def noop_usage(*_a, **_k):
        return None

    monkeypatch.setattr(sched, "_append_usage_status", noop_usage)
    monkeypatch.setattr(sched, "_sync_board_card_for_task", lambda *_a, **_k: None)

    import asyncio as _a
    _a.run(sched._run(task, acc, log_path, auto=False, container_name="coderfleet-alice"))

    assert len(rt.execs) == 1
    assert sched.get_task(task.id).status == TaskStatus.failed


# ── 临时会话（keep-container）路径：通过 seam 驱动，无全局 monkeypatch ──────
def test_ephemeral_keep_container_via_fake_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from coderfleet.server.models import Conversation

    # 队列：start 容器（communicate 返回 id）、然后 exec 进容器
    rt = FakeRuntime().queue(
        FakeProcess(stdout_chunks=[b"container-id\n"], returncode=0),
        FakeProcess(returncode=0),
    )
    sched = Scheduler(tmp_path, runtime=rt)

    conv = Conversation(
        id="conv-eph",
        name="scratch",
        account="alice",
        type=AccountType.claude,
        project="ephemeral",
        ephemeral=True,
        ephemeral_retention="keep_until_ttl",
        ephemeral_ttl_minutes=45,
    )
    conv.save(sched.conversations_dir)
    task = Task(
        id="task-eph-seam",
        status=TaskStatus.running,
        account="alice",
        type=AccountType.claude,
        prompt="hello",
        project=str(sched._get_session_dir(conv.id)),
        conversation_id=conv.id,
        ephemeral=True,
        execution_mode="ephemeral",
        ephemeral_retention="keep_until_ttl",
        ephemeral_ttl_minutes=45,
    )
    task.save(sched.tasks_dir)
    acc = Account(name="alice", type=AccountType.claude)
    log_path = sched.get_log_path(task.id)
    sched._write_log_header(log_path, task, acc)

    monkeypatch.setattr(sched, "_get_ephemeral_network", lambda _acc: None)
    monkeypatch.setattr(sched, "_get_account_image", lambda _acc, _project=None: "coderfleet:test")

    import asyncio as _a
    _a.run(sched._run_ephemeral_task(
        task, acc, log_path, auto=False,
        conversation=conv, session_dir=sched._get_session_dir(conv.id),
    ))

    # seam 记录：一次 detached run（启动会话容器）+ 一次 exec（跑任务）
    assert len(rt.runs) == 1 and rt.runs[0].detached is True and rt.runs[0].remove is False
    assert rt.runs[0].name == "coderfleet-eph-session-conv-eph"
    assert len(rt.execs) == 1 and rt.execs[0][0] == "coderfleet-eph-session-conv-eph"
    assert rt.execs[0][1][:2] == ["bash", "-c"]

    saved_conv = sched.get_conversation(conv.id)
    assert saved_conv.ephemeral_container_name == "coderfleet-eph-session-conv-eph"
    assert saved_conv.ephemeral_expires_at
    assert sched.get_task(task.id).status == TaskStatus.done
