from __future__ import annotations

import asyncio
import os
import shlex
import struct
import sys
from dataclasses import dataclass
from pathlib import Path

_WINDOWS = sys.platform == "win32"

if not _WINDOWS:
    import fcntl
    import pty
    import signal
    import termios

from coderfleet.server import docker_mgr
from coderfleet.server.models import Account, Project
from coderfleet.server.scheduler import Scheduler


@dataclass(frozen=True)
class TerminalTarget:
    project: Project
    account: Account
    container_name: str
    container_workdir: str
    command: list[str]


def build_terminal_command(container_name: str, container_workdir: str) -> list[str]:
    return ["docker", "exec", "-it", "-w", container_workdir, container_name, "bash", "-l"]


def resolve_terminal_target(scheduler: Scheduler, project_name: str) -> TerminalTarget:
    project = scheduler.find_project_by_name(project_name)
    if project is None:
        raise ValueError(f"项目 '{project_name}' 不存在")

    account = next((acc for acc in scheduler.get_accounts() if acc.name == project.account), None)
    if account is None:
        raise ValueError(f"账号 '{project.account}' 不存在")

    container_name = project.container_name(account.type)
    if not docker_mgr.is_container_running(container_name):
        raise RuntimeError(f"容器 {container_name} 未运行")

    container_workdir = scheduler.container_workdir_for_project(project, project.path)
    command = build_terminal_command(container_name, container_workdir)
    return TerminalTarget(
        project=project,
        account=account,
        container_name=container_name,
        container_workdir=container_workdir,
        command=command,
    )


def resize_pty(fd: int, cols: int, rows: int) -> None:
    if _WINDOWS or cols <= 0 or rows <= 0:
        return
    size = struct.pack("HHHH", rows, cols, 0, 0)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, size)  # type: ignore[name-defined]


class TerminalSession:
    def __init__(self, command: list[str], project_name: str):
        self.command = command
        self.project_name = project_name
        self.master_fd: int | None = None
        self.child_pid: int | None = None

    def start(self) -> None:
        if _WINDOWS:
            raise RuntimeError("In-browser terminal is not supported on Windows")
        if self.master_fd is not None or self.child_pid is not None:
            raise RuntimeError("terminal session already started")

        child_pid, master_fd = pty.fork()  # type: ignore[name-defined]
        if child_pid == 0:
            os.execvp(self.command[0], self.command)
        self.child_pid = child_pid
        self.master_fd = master_fd
        os.set_blocking(master_fd, False)

    async def read(self) -> bytes:
        if self.master_fd is None:
            return b""
        return await _read_pty_fd(self.master_fd)

    def write(self, data: str) -> None:
        if self.master_fd is None or not data:
            return
        os.write(self.master_fd, data.encode("utf-8", errors="ignore"))

    def resize(self, cols: int, rows: int) -> None:
        if self.master_fd is None:
            return
        resize_pty(self.master_fd, cols, rows)

    def close(self) -> None:
        child_pid = self.child_pid
        master_fd = self.master_fd
        self.child_pid = None
        self.master_fd = None

        if child_pid is not None:
            try:
                os.kill(child_pid, signal.SIGHUP)  # type: ignore[name-defined]
            except ProcessLookupError:
                pass
            except OSError:
                pass
            try:
                os.waitpid(child_pid, os.WNOHANG)
            except ChildProcessError:
                pass
            except OSError:
                pass

        if master_fd is not None:
            try:
                os.close(master_fd)
            except OSError:
                pass


async def _read_pty_fd(master_fd: int) -> bytes:
    """Event-driven PTY read via epoll/kqueue (zero CPU when idle).

    Uses asyncio's add_reader so the coroutine is suspended until the kernel
    signals that data is available, instead of spinning in a poll loop.
    Falls back to a 20 ms sleep on platforms without selector-based event
    loops (e.g. Windows ProactorEventLoop).
    """
    loop = asyncio.get_running_loop()
    try:
        fut: asyncio.Future[None] = loop.create_future()

        def _on_readable() -> None:
            loop.remove_reader(master_fd)
            if not fut.done():
                fut.set_result(None)

        loop.add_reader(master_fd, _on_readable)
        try:
            await fut
        except asyncio.CancelledError:
            loop.remove_reader(master_fd)
            raise
    except NotImplementedError:
        await asyncio.sleep(0.02)

    try:
        return os.read(master_fd, 4096)
    except (BlockingIOError, OSError):
        return b""


async def is_tmux_session_alive(container_name: str, session_name: str) -> bool:
    """Return True if the named tmux session exists inside the container."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "exec", container_name,
            "tmux", "has-session", "-t", session_name,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=5)
        return proc.returncode == 0
    except Exception:
        return False


async def setup_tmux_session(
    container_name: str,
    session_name: str,
    workdir: str,
    log_path_in_container: str,
) -> None:
    """
    Create the tmux session (if it doesn't exist) and wire up pipe-pane for
    persistent transcript logging.  Safe to call on every reconnect.
    """
    # Create session detached if it doesn't exist yet.
    # Pass locale and TERM so bash readline inside the session handles UTF-8 properly.
    create_cmd = (
        f"tmux has-session -t {shlex.quote(session_name)} 2>/dev/null || "
        f"tmux new-session -d -s {shlex.quote(session_name)} -c {shlex.quote(workdir)} "
        f"-e LANG=C.UTF-8 -e LC_ALL=C.UTF-8 -e TERM=xterm-256color"
    )
    proc = await asyncio.create_subprocess_exec(
        "docker", "exec", container_name, "bash", "-c", create_cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await asyncio.wait_for(proc.wait(), timeout=10)

    options_cmd = (
        f"tmux set-option -t {shlex.quote(session_name)} mouse off && "
        f"tmux set-option -t {shlex.quote(session_name)} history-limit 50000"
    )
    proc_options = await asyncio.create_subprocess_exec(
        "docker", "exec", container_name, "bash", "-c", options_cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await asyncio.wait_for(proc_options.wait(), timeout=10)

    # Ensure log directory exists and enable pipe-pane (idempotent)
    log_dir = str(Path(log_path_in_container).parent)
    pipe_cmd = (
        f"mkdir -p {shlex.quote(log_dir)} && "
        f"tmux pipe-pane -o -t {shlex.quote(session_name)} "
        f"'cat >> {shlex.quote(log_path_in_container)} 2>/dev/null'"
    )
    proc2 = await asyncio.create_subprocess_exec(
        "docker", "exec", container_name, "bash", "-c", pipe_cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await asyncio.wait_for(proc2.wait(), timeout=10)


class TmuxTerminalSession:
    """
    Like TerminalSession but attaches to a named tmux session inside a container.
    Closing this session detaches from tmux without killing the running process.
    """

    def __init__(self, container_name: str, session_name: str, workdir: str):
        self.container_name = container_name
        self.session_name   = session_name
        self.workdir        = workdir
        self.master_fd: int | None = None
        self.child_pid: int | None = None

    def start(self) -> None:
        if _WINDOWS:
            raise RuntimeError("In-browser terminal is not supported on Windows")
        if self.master_fd is not None or self.child_pid is not None:
            raise RuntimeError("tmux terminal session already started")

        # tmux new-session -A: create if absent, attach if present.
        # Pass TERM and locale so xterm-256color features and UTF-8 input work correctly.
        command = [
            "docker", "exec", "-it",
            "-e", "TERM=xterm-256color",
            "-e", "LANG=C.UTF-8",
            "-e", "LC_ALL=C.UTF-8",
            self.container_name,
            "tmux", "new-session", "-A", "-s", self.session_name,
        ]
        child_pid, master_fd = pty.fork()  # type: ignore[name-defined]
        if child_pid == 0:
            os.execvp(command[0], command)
        self.child_pid = child_pid
        self.master_fd = master_fd
        os.set_blocking(master_fd, False)

    async def read(self) -> bytes:
        if self.master_fd is None:
            return b""
        return await _read_pty_fd(self.master_fd)

    def write(self, data: str) -> None:
        if self.master_fd is None or not data:
            return
        os.write(self.master_fd, data.encode("utf-8", errors="ignore"))

    def resize(self, cols: int, rows: int) -> None:
        if self.master_fd is None:
            return
        resize_pty(self.master_fd, cols, rows)

    def close(self) -> None:
        """
        Detach from tmux (Ctrl-B d) so the session keeps running, then
        close our end of the pty.  We do NOT send SIGHUP.
        """
        master_fd = self.master_fd
        child_pid = self.child_pid
        self.master_fd = None
        self.child_pid = None

        if master_fd is not None:
            # Send tmux detach key sequence (prefix + d)
            try:
                os.write(master_fd, b"\x02d")
            except OSError:
                pass
            try:
                os.close(master_fd)
            except OSError:
                pass

        # The docker exec process exits naturally after detach; just reap it.
        if child_pid is not None:
            try:
                os.waitpid(child_pid, os.WNOHANG)
            except (ChildProcessError, OSError):
                pass
