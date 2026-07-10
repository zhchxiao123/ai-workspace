"""
Track cancellable project image builds.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol


class CancellableProcess(Protocol):
    returncode: int | None

    def terminate(self) -> None: ...

    def kill(self) -> None: ...

    async def wait(self) -> int: ...


@dataclass
class ImageBuildRecord:
    build_id: str
    project_name: str
    process: CancellableProcess
    cancel_requested: bool = False


@dataclass(frozen=True)
class ImageBuildCancelResult:
    cancelled: bool
    message: str


class ImageBuildRegistry:
    def __init__(self) -> None:
        self._records: dict[str, ImageBuildRecord] = {}

    def track(self, build_id: str, project_name: str, process: CancellableProcess) -> None:
        self._records[build_id] = ImageBuildRecord(
            build_id=build_id,
            project_name=project_name,
            process=process,
        )

    def get(self, build_id: str) -> ImageBuildRecord | None:
        return self._records.get(build_id)

    def forget(self, build_id: str) -> None:
        self._records.pop(build_id, None)

    def was_cancelled(self, build_id: str) -> bool:
        record = self._records.get(build_id)
        return record.cancel_requested if record is not None else False

    async def cancel(self, build_id: str) -> ImageBuildCancelResult:
        """终止正在跑的构建子进程。

        不在这里 forget 记录——驱动子进程的后台任务（run_image_build）才是记录的
        唯一属主，它在子进程退出后读取 was_cancelled() 决定最终状态，再自己 forget()。
        如果 cancel() 也 forget，两边谁先跑到就会产生竞态，且丢失「这是被取消的」信号。
        """
        record = self._records.get(build_id)
        if record is None:
            return ImageBuildCancelResult(False, "没有正在构建的镜像")

        record.cancel_requested = True
        proc = record.process
        if proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
        return ImageBuildCancelResult(True, "已停止构建")
