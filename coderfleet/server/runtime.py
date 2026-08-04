"""
runtime.py — 容器运行时 seam

任务执行的两条路径（持久容器 `docker exec` 与临时容器 `docker run`）最终都落在同一个
OS 调用上：``asyncio.create_subprocess_exec(*docker_args, ...)``。本模块把「如何生成
docker argv 并启动子进程」收拢到一个接口后面，调度器只描述「要跑什么」（镜像、命令、
环境变量、挂载、网络），docker 的机械细节（argv、--rm、--pull never、-e k=v）全部藏进
DockerRuntime 实现里。

- DockerRuntime —— 生产实现：唯一拼装 docker argv、真正调用 OS 的地方。
- LocalRuntime  —— 生产实现：本地执行模式，不经过 Docker，直接在宿主机跑子进程。
                   ContainerSpec 的容器专属字段（image/mounts/network）对它没有意义，
                   调用方只需传 command/env/workdir；spec.name（run）/container（exec）
                   被当作进程注册表的逻辑 key，不参与实际命令拼装。
- FakeRuntime  —— 测试实现：记录每次 ContainerSpec / exec 调用，返回预先脚本化的进程，
                   让调度器的整条任务生命周期无需 Docker 即可测试。

三个 adapter 实现同一个 Protocol，让这个 seam 是真的（adapters, not branches）。
"""
from __future__ import annotations

import asyncio
import os
import signal
import subprocess
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

# docker exec / run 的 stdout 统一走管道、stderr 并入 stdout —— 与迁移前逐处 create_subprocess_exec
# 的参数保持一致。
_PIPE = asyncio.subprocess.PIPE
_STDOUT = asyncio.subprocess.STDOUT


@dataclass
class ContainerSpec:
    """描述一次 ``docker run`` 需要的一切 —— 用领域词汇，而非 docker argv。"""

    image: str
    command: list[str]                                   # 传给容器的 argv（如 ["bash", "-c", payload]）
    workdir: str = "/workspace"
    env: dict[str, str] = field(default_factory=dict)
    mounts: list[tuple[str, str]] = field(default_factory=list)  # (host, container)
    network: str | None = None
    name: str | None = None
    remove: bool = False                                 # --rm
    detached: bool = False                               # -d


@runtime_checkable
class Process(Protocol):
    """对已启动进程的把手。``asyncio.subprocess.Process`` 天然满足它，测试用 FakeProcess。"""

    pid: int
    returncode: int | None
    stdout: object | None

    async def wait(self) -> int: ...

    async def communicate(self) -> tuple[bytes, bytes]: ...


class ContainerRuntime(Protocol):
    """容器运行时 seam。调度器只依赖这 4 个方法。"""

    async def run(self, spec: ContainerSpec) -> Process: ...

    async def exec(
        self,
        container: str,
        command: list[str],
        env: dict[str, str] | None = None,
        workdir: str = "",
    ) -> Process: ...

    def is_running(self, container: str) -> bool: ...

    async def remove(self, container: str) -> None: ...


class DockerRuntime:
    """生产实现：拼装 docker argv 并启动子进程。唯一触碰 docker CLI 的地方。"""

    # ── 纯函数：argv 拼装（无副作用，便于直接断言）────────────────────────
    @staticmethod
    def build_run_argv(spec: ContainerSpec) -> list[str]:
        argv: list[str] = ["docker", "run", "--pull", "never"]
        if spec.remove:
            argv.append("--rm")
        if spec.name:
            argv += ["--name", spec.name]
        if spec.detached:
            argv.append("-d")
        for host, cont in spec.mounts:
            argv += ["-v", f"{host}:{cont}"]
        if spec.network:
            argv += ["--network", spec.network]
        for k, v in spec.env.items():
            argv += ["-e", f"{k}={v}"]
        if spec.workdir:
            argv += ["-w", spec.workdir]
        argv.append(spec.image)
        argv += list(spec.command)
        return argv

    @staticmethod
    def build_exec_argv(
        container: str,
        command: list[str],
        env: dict[str, str] | None = None,
        workdir: str = "",
    ) -> list[str]:
        argv: list[str] = ["docker", "exec"]
        for k, v in (env or {}).items():
            argv += ["-e", f"{k}={v}"]
        if workdir:
            argv += ["-w", workdir]
        argv.append(container)
        argv += list(command)
        return argv

    # ── 接口实现 ────────────────────────────────────────────────────────
    async def run(self, spec: ContainerSpec) -> Process:
        return await asyncio.create_subprocess_exec(
            *self.build_run_argv(spec), stdout=_PIPE, stderr=_STDOUT
        )

    async def exec(
        self,
        container: str,
        command: list[str],
        env: dict[str, str] | None = None,
        workdir: str = "",
    ) -> Process:
        return await asyncio.create_subprocess_exec(
            *self.build_exec_argv(container, command, env, workdir),
            stdout=_PIPE,
            stderr=_STDOUT,
        )

    def is_running(self, container: str) -> bool:
        result = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Running}}", container],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.returncode == 0 and result.stdout.strip().lower() == "true"

    async def remove(self, container: str) -> None:
        proc = await asyncio.create_subprocess_exec(
            "docker", "rm", "-f", container,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()


class LocalRuntime:
    """本地执行实现：直接在宿主机跑子进程，不经过 Docker。

    没有容器可以 inspect，存活性判断靠自己维护的「逻辑名 → pid」注册表 + ``os.kill(pid, 0)``
    探活；``remove`` 对应 ``SIGTERM``。调用方（Scheduler）负责只在 ``ContainerSpec``/``exec``
    里传 command/env/workdir —— image/mounts/network 这些字段本来就是 Docker 专属的，这里
    直接忽略，不做「本地场景下静默兼容」的特殊处理。
    """

    def __init__(self) -> None:
        self._pids: dict[str, int] = {}

    @staticmethod
    def _merged_env(extra: dict[str, str] | None) -> dict[str, str]:
        env = dict(os.environ)
        env.update(extra or {})
        return env

    async def run(self, spec: ContainerSpec) -> Process:
        proc = await asyncio.create_subprocess_exec(
            *spec.command,
            cwd=spec.workdir or None,
            env=self._merged_env(spec.env),
            stdout=_PIPE,
            stderr=_STDOUT,
        )
        if spec.name:
            self._pids[spec.name] = proc.pid
        return proc

    async def exec(
        self,
        container: str,
        command: list[str],
        env: dict[str, str] | None = None,
        workdir: str = "",
    ) -> Process:
        proc = await asyncio.create_subprocess_exec(
            *command,
            cwd=workdir or None,
            env=self._merged_env(env),
            stdout=_PIPE,
            stderr=_STDOUT,
        )
        if container:
            self._pids[container] = proc.pid
        return proc

    def is_running(self, container: str) -> bool:
        pid = self._pids.get(container)
        if pid is None:
            return False
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True  # 进程存在，只是不属于当前 OS 用户，signal 被拒绝不代表进程死了
        return True

    async def remove(self, container: str) -> None:
        pid = self._pids.pop(container, None)
        if pid is None:
            return
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass


# ── 测试用 adapter ──────────────────────────────────────────────────────
class _FakeReader:
    """异步 stdout：按预置 chunk 逐块吐出，耗尽后返回 b''。"""

    def __init__(self, chunks: list[bytes] | None = None) -> None:
        self._chunks = list(chunks or [])

    async def read(self, n: int = -1) -> bytes:
        await asyncio.sleep(0)
        if not self._chunks:
            return b""
        if n is None or n < 0:
            data = b"".join(self._chunks)
            self._chunks = []
            return data
        return self._chunks.pop(0)


class FakeProcess:
    """脚本化的进程把手，形状与 asyncio.subprocess.Process 一致。"""

    def __init__(
        self,
        stdout_chunks: list[bytes] | None = None,
        returncode: int = 0,
        pid: int = 4321,
    ) -> None:
        self.pid = pid
        self.returncode: int | None = None
        self._rc = returncode
        self._chunks = list(stdout_chunks or [])
        self.stdout = _FakeReader(self._chunks)

    async def wait(self) -> int:
        self.returncode = self._rc
        return self._rc

    async def communicate(self) -> tuple[bytes, bytes]:
        self.returncode = self._rc
        return b"".join(self._chunks), b""


class FakeRuntime:
    """测试实现：记录每次调用，按队列返回 FakeProcess，不碰 Docker。"""

    def __init__(self) -> None:
        self.runs: list[ContainerSpec] = []
        self.execs: list[tuple[str, list[str], dict[str, str], str]] = []
        self.removed: list[str] = []
        self.running: set[str] = set()          # 视为「正在运行」的容器名
        self._queue: list[FakeProcess] = []

    def queue(self, *procs: FakeProcess) -> "FakeRuntime":
        """预置接下来 run/exec 依次返回的进程；耗尽后返回默认退出码 0 的进程。"""
        self._queue.extend(procs)
        return self

    def _next(self) -> FakeProcess:
        return self._queue.pop(0) if self._queue else FakeProcess()

    async def run(self, spec: ContainerSpec) -> Process:
        self.runs.append(spec)
        return self._next()

    async def exec(
        self,
        container: str,
        command: list[str],
        env: dict[str, str] | None = None,
        workdir: str = "",
    ) -> Process:
        self.execs.append((container, list(command), dict(env or {}), workdir))
        return self._next()

    def is_running(self, container: str) -> bool:
        return container in self.running

    async def remove(self, container: str) -> None:
        self.removed.append(container)
        self.running.discard(container)
