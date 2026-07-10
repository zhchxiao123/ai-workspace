from __future__ import annotations

import asyncio

from coderfleet.server.image_builds import ImageBuildRegistry


class FakeBuildProcess:
    def __init__(self) -> None:
        self.returncode = None
        self.terminated = False
        self.killed = False

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True

    async def wait(self) -> int:
        self.returncode = 143
        return self.returncode


def test_cancel_tracked_image_build_terminates_process_and_marks_it_cancelled() -> None:
    """cancel() 不再自己 forget 记录——生命周期唯一属主是驱动子进程的后台任务
    （run_image_build），它在子进程结束后读 was_cancelled() 决定最终状态，
    再由自己 forget()。这样断线不会打断构建，取消也不会有谁先 forget 的竞态。"""
    registry = ImageBuildRegistry()
    proc = FakeBuildProcess()
    registry.track("build-1", "repo", proc)

    result = asyncio.run(registry.cancel("build-1"))

    assert result.cancelled is True
    assert proc.terminated is True
    assert registry.get("build-1") is not None
    assert registry.was_cancelled("build-1") is True

    registry.forget("build-1")
    assert registry.get("build-1") is None
    assert registry.was_cancelled("build-1") is False


def test_cancel_unknown_image_build_reports_not_found() -> None:
    registry = ImageBuildRegistry()

    result = asyncio.run(registry.cancel("missing"))

    assert result.cancelled is False
    assert result.message == "没有正在构建的镜像"
