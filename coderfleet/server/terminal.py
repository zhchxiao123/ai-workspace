from __future__ import annotations

import asyncio
import fcntl
import os
import pty
import signal
import struct
import termios
from dataclasses import dataclass

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
    if cols <= 0 or rows <= 0:
        return
    size = struct.pack("HHHH", rows, cols, 0, 0)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, size)


class TerminalSession:
    def __init__(self, command: list[str], project_name: str):
        self.command = command
        self.project_name = project_name
        self.master_fd: int | None = None
        self.child_pid: int | None = None

    def start(self) -> None:
        if self.master_fd is not None or self.child_pid is not None:
            raise RuntimeError("terminal session already started")

        child_pid, master_fd = pty.fork()
        if child_pid == 0:
            os.execvp(self.command[0], self.command)
        self.child_pid = child_pid
        self.master_fd = master_fd
        os.set_blocking(master_fd, False)

    async def read(self) -> bytes:
        if self.master_fd is None:
            return b""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._read_once)

    def _read_once(self) -> bytes:
        if self.master_fd is None:
            return b""
        try:
            return os.read(self.master_fd, 4096)
        except BlockingIOError:
            return b""
        except OSError:
            return b""

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
                os.kill(child_pid, signal.SIGHUP)
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
