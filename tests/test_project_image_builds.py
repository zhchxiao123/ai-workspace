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


def test_cancel_tracked_image_build_terminates_process_and_forgets_it() -> None:
    registry = ImageBuildRegistry()
    proc = FakeBuildProcess()
    registry.track("build-1", "repo", proc)

    result = asyncio.run(registry.cancel("build-1"))

    assert result.cancelled is True
    assert proc.terminated is True
    assert registry.get("build-1") is None


def test_cancel_unknown_image_build_reports_not_found() -> None:
    registry = ImageBuildRegistry()

    result = asyncio.run(registry.cancel("missing"))

    assert result.cancelled is False
    assert result.message == "没有正在构建的镜像"
