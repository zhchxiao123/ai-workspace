"""test_runtime.py — 容器运行时 seam 的单元测试。"""
from __future__ import annotations

import asyncio
import json

from pathlib import Path

import pytest

from coderfleet.server.models import (
    Account,
    AccountAuth,
    AccountProxy,
    AccountRuntime,
    AccountType,
    Project,
    Task,
    TaskStatus,
)
from coderfleet.server.runtime import (
    ContainerSpec,
    DockerRuntime,
    FakeProcess,
    FakeRuntime,
    LocalRuntime,
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
    task.save(sched.tasks_dir)  # 真实调度路径在进 _run() 前就已落盘（running 状态）
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


def test_stream_container_log_does_not_corrupt_utf8_char_split_across_poll_tick(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Regression test for a real, reproducible corruption. _stream_container_log
    polls host_log's byte size every 0.3s and copies whatever's new to
    log_path. The old code decoded each raw byte-range chunk independently
    (`new_bytes.decode("utf-8", errors="replace")`) before writing it. If a
    poll tick's byte boundary lands in the middle of a multi-byte UTF-8
    character — routine for this codebase, whose task prompts/content are
    frequently Chinese — decoding the two halves independently replaces that
    character with U+FFFD garbage on both sides of the cut; concatenating the
    (already-decoded) halves afterward cannot undo it. If the corruption
    lands near a JSON structural byte the line fails to JSON.parse entirely
    and degrades to raw/garbled display — this is what was actually observed
    after CC's tool_use_result payloads grew large enough to make an unlucky
    mid-character poll tick likely (bigger lines take longer to write, so
    more polls get a chance to land mid-line/mid-character). The fix defers
    decoding until a complete, newline-terminated chunk is buffered, so it
    only ever decodes byte ranges that start/end on valid UTF-8 boundaries.

    Drives the real polling loop with asyncio.sleep replaced by a
    call-counted fake, so the two writes to host_log land deterministically
    on either side of one specific poll tick instead of racing on real
    wall-clock timing.
    """
    real_sleep = asyncio.sleep
    call_count = 0

    text_val = "写周报总结进展完成情况"
    line = (
        json.dumps(
            {"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": text_val},
            ]}},
            ensure_ascii=False,
        ).encode("utf-8")
        + b"\n"
    )
    split_at = line.index(text_val.encode("utf-8")) + 1  # 切在第一个中文字符的 3 字节中间
    first_half, second_half = line[:split_at], line[split_at:]

    host_log = tmp_path / "host.log"
    host_exit = tmp_path / "host.exit"
    host_log.write_bytes(first_half)  # 轮询循环启动前，host_log 里已经是"半个字符"

    async def controlled_sleep(_seconds: float) -> None:
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            with host_log.open("ab") as f:
                f.write(second_half)
        elif call_count == 3:
            host_exit.write_text("0")
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", controlled_sleep)

    sched = Scheduler(tmp_path)
    task, acc, log_path = _mk_task_acc(sched)

    async def noop_usage(*_a, **_k):
        return None

    monkeypatch.setattr(sched, "_append_usage_status", noop_usage)
    monkeypatch.setattr(sched, "_sync_board_card_for_task", lambda *_a, **_k: None)

    asyncio.run(asyncio.wait_for(
        sched._stream_container_log(task, acc, log_path, host_log, host_exit, conversation=None),
        timeout=5,
    ))

    content = log_path.read_text(encoding="utf-8")
    body_lines = [l for l in content.splitlines() if l.startswith("{")]
    assert len(body_lines) == 1, f"expected one JSON line, got: {body_lines!r}"
    parsed = json.loads(body_lines[0])
    assert parsed["message"]["content"][0]["content"] == text_val


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


# ── git 分支探测（issue #87）───────────────────────────────────────────
def _run_with_workdir(
    sched: Scheduler, task: Task, acc: Account, log_path: Path,
    monkeypatch: pytest.MonkeyPatch, container_workdir: str = "/workspace",
) -> None:
    async def fake_stream(t, a, lp, host_log, host_exit, conv):
        t.update_status(TaskStatus.done, sched.tasks_dir)

    monkeypatch.setattr(sched, "_stream_container_log", fake_stream)
    monkeypatch.setattr(sched, "_get_project_root", lambda _t: Path("/tmp"))

    import asyncio as _a
    _a.run(sched._run(
        task, acc, log_path, auto=False,
        container_workdir=container_workdir, container_name="coderfleet-alice",
    ))


def test_run_probes_git_branch_when_workdir_known(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rt = FakeRuntime().queue(
        FakeProcess(stdout_chunks=[b"feature/foo\nabc1234\n/workspace/.git\n"], returncode=0),
        FakeProcess(returncode=0),
    )
    sched = Scheduler(tmp_path, runtime=rt)
    task, acc, log_path = _mk_task_acc(sched)

    _run_with_workdir(sched, task, acc, log_path, monkeypatch)

    assert len(rt.execs) == 2
    probe_container, probe_cmd, _, probe_workdir = rt.execs[0]
    assert probe_container == "coderfleet-alice"
    assert "git" in probe_cmd[-1]
    # workdir 走 runtime.exec 自身的 -w 参数，不再手写 `git -C`
    assert probe_workdir == "/workspace"
    saved = sched.get_task(task.id)
    assert saved.git_branch == "feature/foo"
    assert saved.git_worktree is False


def test_run_probes_git_worktree_true_when_git_dir_has_worktrees(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rt = FakeRuntime().queue(
        FakeProcess(
            stdout_chunks=[b"feature/bar\nabc1234\n/workspace/.git/worktrees/feature-bar\n"],
            returncode=0,
        ),
        FakeProcess(returncode=0),
    )
    sched = Scheduler(tmp_path, runtime=rt)
    task, acc, log_path = _mk_task_acc(sched)

    _run_with_workdir(sched, task, acc, log_path, monkeypatch)

    saved = sched.get_task(task.id)
    assert saved.git_branch == "feature/bar"
    assert saved.git_worktree is True


def test_run_probes_git_detached_head_falls_back_to_short_sha(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """detached HEAD：--abbrev-ref 只会给字面量 "HEAD"，不能把它当分支名展示，退化用短 SHA。"""
    rt = FakeRuntime().queue(
        FakeProcess(stdout_chunks=[b"HEAD\nabc1234\n/workspace/.git\n"], returncode=0),
        FakeProcess(returncode=0),
    )
    sched = Scheduler(tmp_path, runtime=rt)
    task, acc, log_path = _mk_task_acc(sched)

    _run_with_workdir(sched, task, acc, log_path, monkeypatch)

    saved = sched.get_task(task.id)
    assert saved.git_branch == "abc1234"


def test_run_leaves_git_branch_empty_when_probe_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """非 git 目录 / git 探测失败：字段留空，任务本身照常成功——探测失败不是任务失败。"""
    rt = FakeRuntime().queue(
        FakeProcess(stdout_chunks=[b""], returncode=128),
        FakeProcess(returncode=0),
    )
    sched = Scheduler(tmp_path, runtime=rt)
    task, acc, log_path = _mk_task_acc(sched)

    _run_with_workdir(sched, task, acc, log_path, monkeypatch)

    saved = sched.get_task(task.id)
    assert saved.git_branch == ""
    assert saved.git_worktree is False
    assert saved.status == TaskStatus.done


def test_run_skips_git_probe_when_workdir_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """container_workdir 未知时不发起探测 exec —— 与既有 _mk_task_acc 调用方式保持一致。"""
    rt = FakeRuntime().queue(FakeProcess(returncode=0))
    sched = Scheduler(tmp_path, runtime=rt)
    task, acc, log_path = _mk_task_acc(sched)

    async def fake_stream(t, a, lp, host_log, host_exit, conv):
        t.update_status(TaskStatus.done, sched.tasks_dir)

    monkeypatch.setattr(sched, "_stream_container_log", fake_stream)
    monkeypatch.setattr(sched, "_get_project_root", lambda _t: tmp_path)

    import asyncio as _a
    _a.run(sched._run(task, acc, log_path, auto=False, container_name="coderfleet-alice"))

    assert len(rt.execs) == 1
    assert sched.get_task(task.id).git_branch == ""


def test_run_probes_git_diff_shortstat_when_uncommitted_changes_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rt = FakeRuntime().queue(
        FakeProcess(
            stdout_chunks=[
                b"feature/foo\nabc1234\n/workspace/.git\n"
                b" 3 files changed, 42 insertions(+), 7 deletions(-)\n"
            ],
            returncode=0,
        ),
        FakeProcess(returncode=0),
    )
    sched = Scheduler(tmp_path, runtime=rt)
    task, acc, log_path = _mk_task_acc(sched)

    _run_with_workdir(sched, task, acc, log_path, monkeypatch)

    saved = sched.get_task(task.id)
    assert saved.git_diff_added == 42
    assert saved.git_diff_removed == 7


def test_run_probes_git_diff_shortstat_insertions_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """只新增没删除时 --shortstat 不会输出 "N deletions(-)" 那一截，必须解析成 0 而不是报错。"""
    rt = FakeRuntime().queue(
        FakeProcess(
            stdout_chunks=[b"feature/foo\nabc1234\n/workspace/.git\n 1 file changed, 5 insertions(+)\n"],
            returncode=0,
        ),
        FakeProcess(returncode=0),
    )
    sched = Scheduler(tmp_path, runtime=rt)
    task, acc, log_path = _mk_task_acc(sched)

    _run_with_workdir(sched, task, acc, log_path, monkeypatch)

    saved = sched.get_task(task.id)
    assert saved.git_diff_added == 5
    assert saved.git_diff_removed == 0


def test_run_probes_git_diff_shortstat_defaults_zero_when_no_uncommitted_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """工作区干净时 `git diff --shortstat HEAD` 本来就没有输出——第 4 行缺失不是探测失败。"""
    rt = FakeRuntime().queue(
        FakeProcess(stdout_chunks=[b"feature/foo\nabc1234\n/workspace/.git\n"], returncode=0),
        FakeProcess(returncode=0),
    )
    sched = Scheduler(tmp_path, runtime=rt)
    task, acc, log_path = _mk_task_acc(sched)

    _run_with_workdir(sched, task, acc, log_path, monkeypatch)

    saved = sched.get_task(task.id)
    assert saved.git_branch == "feature/foo"
    assert saved.git_diff_added == 0
    assert saved.git_diff_removed == 0


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
    # 没有 project 上下文（本用例未传 project=）：不探测，仍是 1 次 exec
    assert sched.get_task(task.id).git_branch == ""


def test_ephemeral_keep_container_probes_git_branch_when_project_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """keep-container 场景下，只要有已解析的 project，就在跑任务前多探测一次分支。"""
    from coderfleet.server.models import Conversation

    rt = FakeRuntime().queue(
        FakeProcess(stdout_chunks=[b"container-id\n"], returncode=0),  # 启动会话容器
        FakeProcess(  # git 探测
            stdout_chunks=[b"main\nabc1234\n/workspace/.git\n 2 files changed, 10 insertions(+), 3 deletions(-)\n"],
            returncode=0,
        ),
        FakeProcess(returncode=0),  # 真正跑任务
    )
    sched = Scheduler(tmp_path, runtime=rt)

    conv = Conversation(
        id="conv-eph-proj",
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
        id="task-eph-proj",
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
    project = Project(name="scratch-proj", account="alice", path=str(sched._get_session_dir(conv.id)))

    monkeypatch.setattr(sched, "_get_ephemeral_network", lambda _acc: None)
    monkeypatch.setattr(sched, "_get_account_image", lambda _acc, _project=None: "coderfleet:test")

    import asyncio as _a
    _a.run(sched._run_ephemeral_task(
        task, acc, log_path, auto=False,
        conversation=conv, session_dir=sched._get_session_dir(conv.id), project=project,
    ))

    assert len(rt.execs) == 2
    probe_container, probe_cmd, _, probe_workdir = rt.execs[0]
    assert probe_container == "coderfleet-eph-session-conv-eph-proj"
    assert "git" in probe_cmd[-1]
    assert probe_workdir == "/workspace"
    saved = sched.get_task(task.id)
    assert saved.git_branch == "main"
    assert saved.git_worktree is False
    assert saved.git_diff_added == 10
    assert saved.git_diff_removed == 3
    assert saved.status == TaskStatus.done


def test_ephemeral_task_mounts_configured_docker_socket(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    socket_path = tmp_path / "docker.sock"
    socket_path.touch()
    (tmp_path / "config.conf").write_text(
        f"DOCKER_SOCKET={socket_path}\n", encoding="utf-8"
    )
    rt = FakeRuntime().queue(FakeProcess(returncode=0))
    sched = Scheduler(tmp_path, runtime=rt)
    task, acc, log_path = _mk_task_acc(sched)
    session_dir = tmp_path / "sessions" / "conv"
    session_dir.mkdir(parents=True)

    monkeypatch.setattr(sched, "_get_ephemeral_network", lambda _acc: None)
    monkeypatch.setattr(sched, "_get_account_image", lambda _acc, _project=None: "coderfleet:test")

    import asyncio as _a
    _a.run(sched._run_ephemeral_task(
        task, acc, log_path, auto=False, session_dir=session_dir,
    ))

    assert len(rt.runs) == 1
    spec = rt.runs[0]
    assert (str(socket_path), "/var/run/docker.sock") in spec.mounts
    assert spec.env["DOCKER_HOST"] == "unix:///var/run/docker.sock"
    assert spec.env["CODERFLEET_HOST_WORKSPACE"] == str(session_dir)


def test_ephemeral_task_injects_configured_container_timezone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "config.conf").write_text(
        "CONTAINER_TIMEZONE=Europe/Paris\n", encoding="utf-8"
    )
    rt = FakeRuntime().queue(FakeProcess(returncode=0))
    sched = Scheduler(tmp_path, runtime=rt)
    task, acc, log_path = _mk_task_acc(sched)

    monkeypatch.setattr(sched, "_get_ephemeral_network", lambda _acc: None)
    monkeypatch.setattr(sched, "_get_account_image", lambda _acc, _project=None: "coderfleet:test")

    import asyncio as _a
    _a.run(sched._run_ephemeral_task(task, acc, log_path, auto=False))

    assert rt.runs[0].env["TZ"] == "Europe/Paris"


def test_ephemeral_task_project_docker_socket_overrides_global(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    global_socket = tmp_path / "global.sock"
    project_socket = tmp_path / "project.sock"
    global_socket.touch()
    project_socket.touch()
    (tmp_path / "config.conf").write_text(
        f"DOCKER_SOCKET={global_socket}\n", encoding="utf-8"
    )
    rt = FakeRuntime().queue(FakeProcess(returncode=0))
    sched = Scheduler(tmp_path, runtime=rt)
    task, acc, log_path = _mk_task_acc(sched)
    session_dir = tmp_path / "sessions" / "conv"
    session_dir.mkdir(parents=True)
    project = Project(
        name="repo",
        account="alice",
        path=str(session_dir),
        docker_socket=str(project_socket),
    )

    monkeypatch.setattr(sched, "_get_ephemeral_network", lambda _acc: None)
    monkeypatch.setattr(sched, "_get_account_image", lambda _acc, _project=None: "coderfleet:test")

    import asyncio as _a
    _a.run(sched._run_ephemeral_task(
        task, acc, log_path, auto=False, session_dir=session_dir, project=project,
    ))

    spec = rt.runs[0]
    assert (str(project_socket), "/var/run/docker.sock") in spec.mounts
    assert (str(global_socket), "/var/run/docker.sock") not in spec.mounts


# ── _append_usage_status / _get_hermes_session_id 走 runtime seam，不再裸拼 docker exec ──
def test_append_usage_status_goes_through_runtime_seam(tmp_path: Path) -> None:
    rt = FakeRuntime().queue(FakeProcess(stdout_chunks=[b"5h limit: 42%"], returncode=0))
    sched = Scheduler(tmp_path, runtime=rt)
    acc = Account(name="alice", type=AccountType.codex)  # only codex has a usage_status_cmd
    log_path = tmp_path / "task.log"
    log_path.write_text("")

    asyncio.run(sched._append_usage_status(log_path, acc, "coderfleet-alice"))

    assert len(rt.execs) == 1
    container, command, _env, _workdir = rt.execs[0]
    assert container == "coderfleet-alice"
    assert command[:2] == ["bash", "-lc"]
    assert "5h limit: 42%" in log_path.read_text()


def test_get_hermes_session_id_goes_through_runtime_seam(tmp_path: Path) -> None:
    payload = json.dumps({"id": "sess-123"})
    rt = FakeRuntime().queue(FakeProcess(stdout_chunks=[payload.encode()], returncode=0))
    sched = Scheduler(tmp_path, runtime=rt)

    session_id = asyncio.run(sched._get_hermes_session_id("coderfleet-alice"))

    assert session_id == "sess-123"
    assert len(rt.execs) == 1
    container, command, _env, _workdir = rt.execs[0]
    assert container == "coderfleet-alice"
    assert command[0:2] == ["bash", "-lc"]
    assert "hermes sessions export" in command[2]


def test_get_hermes_session_id_swallows_errors(tmp_path: Path) -> None:
    rt = FakeRuntime().queue(FakeProcess(stdout_chunks=[b"not json"], returncode=0))
    sched = Scheduler(tmp_path, runtime=rt)

    session_id = asyncio.run(sched._get_hermes_session_id("coderfleet-alice"))

    assert session_id == ""


# ── LocalRuntime：真的跑子进程，不碰 Docker ────────────────────────────
def test_local_runtime_run_executes_and_captures_stdout() -> None:
    rt = LocalRuntime()

    async def drive() -> bytes:
        proc = await rt.run(ContainerSpec(
            image="unused", command=["sh", "-c", "echo hello-local"], name="probe-1",
        ))
        out, _ = await proc.communicate()
        return out

    assert asyncio.run(drive()).strip() == b"hello-local"


def test_local_runtime_exec_ignores_container_arg_in_argv_but_passes_env() -> None:
    rt = LocalRuntime()

    async def drive() -> bytes:
        proc = await rt.exec("logical-id", ["sh", "-c", "echo $FOO"], env={"FOO": "bar"})
        out, _ = await proc.communicate()
        return out

    assert asyncio.run(drive()).strip() == b"bar"


def test_local_runtime_run_uses_workdir_as_cwd(tmp_path: Path) -> None:
    rt = LocalRuntime()
    workdir = tmp_path / "ws"
    workdir.mkdir()
    (workdir / "marker.txt").write_text("x")

    async def drive() -> bytes:
        proc = await rt.run(ContainerSpec(
            image="unused", command=["ls"], workdir=str(workdir), name="probe-2",
        ))
        out, _ = await proc.communicate()
        return out

    assert b"marker.txt" in asyncio.run(drive())


def test_local_runtime_is_running_tracks_registered_pid() -> None:
    rt = LocalRuntime()
    assert rt.is_running("nope") is False

    async def drive() -> None:
        proc = await rt.run(ContainerSpec(image="unused", command=["sleep", "5"], name="long-running"))
        assert rt.is_running("long-running") is True
        proc.kill()
        await proc.wait()

    asyncio.run(drive())


def test_local_runtime_remove_terminates_process_and_is_noop_for_unknown() -> None:
    rt = LocalRuntime()

    async def drive() -> int:
        proc = await rt.run(ContainerSpec(image="unused", command=["sleep", "5"], name="killme"))
        await rt.remove("killme")
        return await asyncio.wait_for(proc.wait(), timeout=2)

    rc = asyncio.run(drive())
    assert rc != 0  # 被 SIGTERM 终止，非正常退出码
    assert rt.is_running("killme") is False

    asyncio.run(rt.remove("never-registered"))  # 未知逻辑名：不抛异常


# ── Scheduler：runtime=local 账号按账号路由到 LocalRuntime，跳过 docker 专属逻辑 ──
def test_local_auth_dir_creates_distinct_per_account_directories(tmp_path: Path) -> None:
    sched = Scheduler(tmp_path)
    alice = Account(name="alice", type=AccountType.claude, runtime=AccountRuntime.local)
    bob   = Account(name="bob",   type=AccountType.codex,  runtime=AccountRuntime.local)

    alice_dir = sched.local_auth_dir(alice)
    bob_dir   = sched.local_auth_dir(bob)

    assert alice_dir != bob_dir
    assert alice_dir.is_dir() and bob_dir.is_dir()
    assert alice_dir.name == ".claude" and bob_dir.name == ".codex"
    assert alice_dir.parent.name == "alice" and bob_dir.parent.name == "bob"


def test_ephemeral_task_local_account_routes_through_local_runtime_not_docker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """runtime=local 的账号：一次性任务经 _runtime_for 路由到 LocalRuntime，self.runtime（docker）完全不被调用。"""
    docker_rt = FakeRuntime()
    local_rt = FakeRuntime().queue(FakeProcess(returncode=0))
    sched = Scheduler(tmp_path, runtime=docker_rt, local_runtime=local_rt)
    task, _acc, log_path = _mk_task_acc(sched)
    acc = Account(name="alice", type=AccountType.claude, runtime=AccountRuntime.local)

    monkeypatch.setattr(sched, "_get_account_image", lambda _acc, _project=None: "unused")

    import asyncio as _a
    _a.run(sched._run_ephemeral_task(task, acc, log_path, auto=False))

    assert len(docker_rt.runs) == 0 and len(docker_rt.execs) == 0
    assert len(local_rt.runs) == 1
    spec = local_rt.runs[0]
    # workdir 换成了真实宿主机目录，不是容器路径 "/workspace"
    assert spec.workdir != "/workspace"
    assert Path(spec.workdir).is_dir()
    # PATH 不被容器专属前缀覆盖 —— 继承宿主机真实 PATH
    assert not spec.command[2].startswith("export PATH=")
    # 凭证隔离：CLAUDE_CONFIG_DIR 指向该账号专属宿主机目录，CODEX_HOME 保持未使用的默认值
    assert spec.env["CLAUDE_CONFIG_DIR"] == str(sched.local_auth_dir(acc))
    assert spec.env["CODEX_HOME"] == "/home/byclaw/.codex"
    assert sched.get_task(task.id).status == TaskStatus.done


def test_ephemeral_task_local_account_ignores_keep_container_session_reuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """local 账号没有"闲置容器反复 exec"这个技巧：即使 retention=keep_until_ttl，也总是单次 run()。"""
    from coderfleet.server.models import Conversation

    local_rt = FakeRuntime().queue(FakeProcess(returncode=0))
    sched = Scheduler(tmp_path, local_runtime=local_rt)

    conv = Conversation(
        id="conv-local-eph", name="scratch", account="alice", type=AccountType.claude,
        project="ephemeral", ephemeral=True,
        ephemeral_retention="keep_until_ttl", ephemeral_ttl_minutes=45,
    )
    conv.save(sched.conversations_dir)
    task = Task(
        id="task-local-eph", status=TaskStatus.running, account="alice", type=AccountType.claude,
        prompt="hello", project=str(sched._get_session_dir(conv.id)), conversation_id=conv.id,
        ephemeral=True, execution_mode="ephemeral",
        ephemeral_retention="keep_until_ttl", ephemeral_ttl_minutes=45,
    )
    task.save(sched.tasks_dir)
    acc = Account(name="alice", type=AccountType.claude, runtime=AccountRuntime.local)
    log_path = sched.get_log_path(task.id)
    sched._write_log_header(log_path, task, acc)

    monkeypatch.setattr(sched, "_get_account_image", lambda _acc, _project=None: "unused")

    import asyncio as _a
    _a.run(sched._run_ephemeral_task(
        task, acc, log_path, auto=False,
        conversation=conv, session_dir=sched._get_session_dir(conv.id),
    ))

    # 只有一次 run（一次性），没有"启动会话容器 + exec 进去"这两步
    assert len(local_rt.runs) == 1
    assert len(local_rt.execs) == 0
    saved_conv = sched.get_conversation(conv.id)
    assert saved_conv.ephemeral_container_name == ""  # keep-container 的记账逻辑没有触发


def test_ephemeral_task_local_claude_relay_sets_proxy_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """local + claude + proxy=relay：仍然设代理环境变量（不依赖 docker 网络探测）。"""
    local_rt = FakeRuntime().queue(FakeProcess(returncode=0))
    sched = Scheduler(tmp_path, local_runtime=local_rt)
    task, _acc, log_path = _mk_task_acc(sched)
    acc = Account(
        name="alice", type=AccountType.claude,
        runtime=AccountRuntime.local, proxy=AccountProxy.relay,
    )

    # 如果实现不小心还调用了 docker inspect 探测网络，这个 monkeypatch 会让测试直接报错，
    # 证明 local 分支确实没有依赖它。
    def _boom(_acc):
        raise AssertionError("local runtime 不应该调用 _get_ephemeral_network(docker inspect)")
    monkeypatch.setattr(sched, "_get_ephemeral_network", _boom)
    monkeypatch.setattr(sched, "_get_account_image", lambda _acc, _project=None: "unused")

    import asyncio as _a
    _a.run(sched._run_ephemeral_task(task, acc, log_path, auto=False))

    spec = local_rt.runs[0]
    assert spec.env["HTTPS_PROXY"].startswith("http://")


def test_ephemeral_task_local_proxy_off_sets_no_proxy_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    local_rt = FakeRuntime().queue(FakeProcess(returncode=0))
    sched = Scheduler(tmp_path, local_runtime=local_rt)
    task, _acc, log_path = _mk_task_acc(sched)
    acc = Account(
        name="alice", type=AccountType.claude,
        runtime=AccountRuntime.local, proxy=AccountProxy.off,
    )
    monkeypatch.setattr(sched, "_get_account_image", lambda _acc, _project=None: "unused")

    import asyncio as _a
    _a.run(sched._run_ephemeral_task(task, acc, log_path, auto=False))

    spec = local_rt.runs[0]
    assert "HTTPS_PROXY" not in spec.env


# ── Account.runtime: 解析 + accounts.conf 序列化往返 ────────────────────
def test_account_from_conf_record_defaults_runtime_to_container() -> None:
    acc = Account.from_conf_record({"NAME": "alice", "TYPE": "claude"})
    assert acc is not None
    assert acc.runtime == AccountRuntime.container


def test_account_from_conf_record_parses_explicit_runtime_local() -> None:
    acc = Account.from_conf_record({"NAME": "alice", "TYPE": "claude", "RUNTIME": "local"})
    assert acc is not None
    assert acc.runtime == AccountRuntime.local


def test_check_account_runtime_proxy_compat_rejects_local_codex_relay() -> None:
    from coderfleet.server.models import check_account_runtime_proxy_compat

    msg = check_account_runtime_proxy_compat(AccountType.codex, AccountRuntime.local, AccountProxy.relay)
    assert msg  # 非空字符串 = 拒绝
    assert "openai/codex#4242" in msg


def test_check_account_runtime_proxy_compat_allows_local_claude_relay() -> None:
    from coderfleet.server.models import check_account_runtime_proxy_compat

    assert check_account_runtime_proxy_compat(AccountType.claude, AccountRuntime.local, AccountProxy.relay) == ""


def test_check_account_runtime_proxy_compat_allows_local_codex_proxy_off() -> None:
    from coderfleet.server.models import check_account_runtime_proxy_compat

    assert check_account_runtime_proxy_compat(AccountType.codex, AccountRuntime.local, AccountProxy.off) == ""


def test_check_account_runtime_proxy_compat_allows_container_codex_relay() -> None:
    """校验只针对 local runtime —— container 场景本来就有 docker 网络层兜底，不受影响。"""
    from coderfleet.server.models import check_account_runtime_proxy_compat

    assert check_account_runtime_proxy_compat(AccountType.codex, AccountRuntime.container, AccountProxy.relay) == ""


def test_save_account_rejects_local_codex_relay(tmp_path: Path) -> None:
    sched = Scheduler(tmp_path)
    with pytest.raises(ValueError, match="openai/codex#4242"):
        sched.save_account(
            "bob", AccountType.codex, auth=AccountAuth.login,
            proxy=AccountProxy.relay, runtime=AccountRuntime.local,
        )
    assert sched.get_accounts() == []  # 拒绝后不留下部分写入的账号行


def test_save_account_round_trips_runtime_through_accounts_conf(tmp_path: Path) -> None:
    sched = Scheduler(tmp_path)
    sched.save_account(
        "alice", AccountType.claude, auth=AccountAuth.login,
        proxy=AccountProxy.relay, runtime=AccountRuntime.local,
    )
    accounts = {a.name: a for a in sched.get_accounts()}
    assert accounts["alice"].runtime == AccountRuntime.local
    assert "RUNTIME=local" in (tmp_path / "accounts.conf").read_text()


def test_account_from_conf_record_defaults_sandbox_confirmed_to_false() -> None:
    acc = Account.from_conf_record({"NAME": "alice", "TYPE": "claude"})
    assert acc is not None
    assert acc.sandbox_confirmed is False


def test_account_from_conf_record_parses_sandbox_confirmed_true() -> None:
    acc = Account.from_conf_record({"NAME": "alice", "TYPE": "claude", "SANDBOX_CONFIRMED": "true"})
    assert acc is not None
    assert acc.sandbox_confirmed is True


def test_save_account_round_trips_sandbox_confirmed(tmp_path: Path) -> None:
    sched = Scheduler(tmp_path)
    sched.save_account(
        "alice", AccountType.claude, auth=AccountAuth.login, proxy=AccountProxy.relay,
        runtime=AccountRuntime.local, sandbox_confirmed=True,
    )
    accounts = {a.name: a for a in sched.get_accounts()}
    assert accounts["alice"].sandbox_confirmed is True
    assert "SANDBOX_CONFIRMED=true" in (tmp_path / "accounts.conf").read_text()
