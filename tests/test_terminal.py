from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from coderfleet.server.models import Account, AccountType, Project
from coderfleet.server.scheduler import Scheduler


def test_terminal_command_enters_project_container_workdir(tmp_path: Path) -> None:
    from coderfleet.server.terminal import build_terminal_command

    sched = Scheduler(tmp_path)
    project = Project(name="repo", account="alice", path=str(tmp_path / "repo"))
    (tmp_path / "repo").mkdir()

    command = build_terminal_command(
        container_name=project.container_name(AccountType.codex),
        container_workdir=sched.container_workdir_for_project(project, project.path),
    )

    assert command == ["docker", "exec", "-it", "-w", "/workspace", "codex-repo", "bash", "-l"]


def test_setup_tmux_session_configures_scrollback_without_mouse_capture(monkeypatch: pytest.MonkeyPatch) -> None:
    from coderfleet.server import terminal

    commands: list[tuple[str, ...]] = []

    class FakeProc:
        returncode = 0

        async def wait(self) -> int:
            return self.returncode

    async def fake_create_subprocess_exec(*args: str, **_kwargs: object) -> FakeProc:
        commands.append(args)
        return FakeProc()

    monkeypatch.setattr(terminal.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    asyncio.run(terminal.setup_tmux_session(
        container_name="codex-repo",
        session_name="cf-test",
        workdir="/workspace",
        log_path_in_container="/workspace/.coderfleet-terminals/cf-test.log",
    ))

    bash_commands = [cmd[-1] for cmd in commands if cmd[:4] == ("docker", "exec", "codex-repo", "bash")]
    assert any("tmux set-option -t cf-test mouse off" in cmd for cmd in bash_commands)
    assert any("tmux set-option -t cf-test history-limit 50000" in cmd for cmd in bash_commands)


def test_terminal_session_resize_delegates_to_pty(monkeypatch: pytest.MonkeyPatch) -> None:
    from coderfleet.server import terminal

    calls: list[tuple[int, int, int]] = []
    session = terminal.TerminalSession(
        command=["docker", "exec", "-it", "codex-repo", "bash", "-l"],
        project_name="repo",
    )
    session.master_fd = 42
    monkeypatch.setattr(terminal, "resize_pty", lambda fd, cols, rows: calls.append((fd, cols, rows)))

    session.resize(cols=120, rows=32)

    assert calls == [(42, 120, 32)]


def test_terminal_session_close_terminates_child(monkeypatch: pytest.MonkeyPatch) -> None:
    from coderfleet.server import terminal

    killed: list[tuple[int, int]] = []
    closed: list[int] = []
    waited: list[tuple[int, int]] = []
    session = terminal.TerminalSession(
        command=["docker", "exec", "-it", "codex-repo", "bash", "-l"],
        project_name="repo",
    )
    session.master_fd = 42
    session.child_pid = 123
    monkeypatch.setattr(terminal.os, "kill", lambda pid, sig: killed.append((pid, sig)))
    monkeypatch.setattr(terminal.os, "close", lambda fd: closed.append(fd))
    monkeypatch.setattr(terminal.os, "waitpid", lambda pid, flags: waited.append((pid, flags)) or (pid, 0))

    session.close()

    assert killed
    assert closed == [42]
    assert waited
    assert session.master_fd is None
    assert session.child_pid is None


def test_resolve_terminal_target_rejects_unknown_project(tmp_path: Path) -> None:
    from coderfleet.server.terminal import resolve_terminal_target

    sched = Scheduler(tmp_path)

    with pytest.raises(ValueError, match="项目 .* 不存在"):
        resolve_terminal_target(sched, "missing")


def test_resolve_terminal_target_rejects_missing_account(tmp_path: Path) -> None:
    from coderfleet.server.terminal import resolve_terminal_target

    sched = Scheduler(tmp_path)
    monkeypatch_project = Project(name="repo", account="alice", path=str(tmp_path / "repo"))
    (tmp_path / "repo").mkdir()
    sched.find_project_by_name = lambda _name: monkeypatch_project  # type: ignore[method-assign]
    sched.get_accounts = lambda: []  # type: ignore[method-assign]

    with pytest.raises(ValueError, match="账号 .* 不存在"):
        resolve_terminal_target(sched, "repo")


def test_resolve_terminal_target_rejects_stopped_container(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from coderfleet.server import terminal
    from coderfleet.server.terminal import resolve_terminal_target

    sched = Scheduler(tmp_path)
    project = Project(name="repo", account="alice", path=str(tmp_path / "repo"))
    (tmp_path / "repo").mkdir()
    sched.find_project_by_name = lambda _name: project  # type: ignore[method-assign]
    sched.get_accounts = lambda: [Account(name="alice", type=AccountType.codex)]  # type: ignore[method-assign]
    monkeypatch.setattr(terminal.docker_mgr, "is_container_running", lambda _name: False)

    with pytest.raises(RuntimeError, match="容器 .* 未运行"):
        resolve_terminal_target(sched, "repo")


def test_resolve_terminal_target_returns_docker_exec_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from coderfleet.server import terminal
    from coderfleet.server.terminal import resolve_terminal_target

    sched = Scheduler(tmp_path)
    project = Project(name="repo", account="alice", path=str(tmp_path / "repo"))
    (tmp_path / "repo").mkdir()
    sched.find_project_by_name = lambda _name: project  # type: ignore[method-assign]
    sched.get_accounts = lambda: [Account(name="alice", type=AccountType.claude)]  # type: ignore[method-assign]
    monkeypatch.setattr(terminal.docker_mgr, "is_container_running", lambda _name: True)

    target = resolve_terminal_target(sched, "repo")

    assert target.project == project
    assert target.account.name == "alice"
    assert target.container_name == "claude-repo"
    assert target.container_workdir == "/workspace"
    assert target.command == ["docker", "exec", "-it", "-w", "/workspace", "claude-repo", "bash", "-l"]
