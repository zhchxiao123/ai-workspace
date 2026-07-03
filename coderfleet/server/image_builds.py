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


@dataclass(frozen=True)
class ImageBuildRecord:
    build_id: str
    project_name: str
    process: CancellableProcess


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

    async def cancel(self, build_id: str) -> ImageBuildCancelResult:
        record = self._records.get(build_id)
        if record is None:
            return ImageBuildCancelResult(False, "没有正在构建的镜像")

        proc = record.process
        try:
            if proc.returncode is None:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=5)
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()
            return ImageBuildCancelResult(True, "已停止构建")
        finally:
            self.forget(build_id)
