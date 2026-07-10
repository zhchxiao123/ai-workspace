"""test_image_build_runner.py — run_image_build() 与请求生命周期解耦的行为。"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from coderfleet.server.image_builds import ImageBuildRegistry
from coderfleet.server.image_build_runner import run_image_build
from coderfleet.server.models import ImageBuild, ImageBuildStatus


class FakeLineStream:
    def __init__(self, lines: list[bytes]) -> None:
        self._lines = list(lines)

    def __aiter__(self) -> "FakeLineStream":
        return self

    async def __anext__(self) -> bytes:
        await asyncio.sleep(0)
        if not self._lines:
            raise StopAsyncIteration
        return self._lines.pop(0)


class FakeBuildProcess:
    def __init__(self, lines: list[bytes], returncode: int = 0) -> None:
        self.stdout = FakeLineStream(lines)
        self._rc = returncode
        self.returncode: int | None = None
        self.terminated = False

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.terminated = True

    async def wait(self) -> int:
        self.returncode = self._rc
        return self._rc


def test_successful_build_persists_log_and_succeeded_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    builds_dir = tmp_path / "builds"
    proc = FakeBuildProcess([b"Step 1/1\n", b"Successfully built abc123\n"], returncode=0)

    async def fake_exec(*_args, **_kwargs):
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    registry = ImageBuildRegistry()
    build = ImageBuild(id="b1", kind="project", project_name="web", image_tag="coderfleet-web:latest")
    on_success_calls = []

    asyncio.run(run_image_build(
        build, builds_dir, tmp_path / "Dockerfile", tmp_path, "linux/amd64",
        registry, on_success=on_success_calls.append,
    ))

    saved = ImageBuild.load(builds_dir / "b1.json")
    assert saved.status == ImageBuildStatus.succeeded
    assert saved.exit_code == 0
    assert saved.finished is not None
    log_text = (builds_dir / "b1.log").read_text(encoding="utf-8")
    assert "Step 1/1" in log_text
    assert "构建完成" in log_text
    assert on_success_calls == ["coderfleet-web:latest"]
    # 构建结束后不应继续占用 registry
    assert registry.get("b1") is None


def test_exception_mid_stream_read_persists_failed_status_instead_of_crashing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """回归测试：async for 读 stdout 时若抛异常，rc 从未被赋值——之前的实现会在
    随后引用 rc 时抛 UnboundLocalError，这个异常发生在 asyncio.create_task 里
    没人 await，会被静默吞掉，构建记录永远卡在 running。"""
    builds_dir = tmp_path / "builds"

    class ExplodingLineStream:
        def __aiter__(self) -> "ExplodingLineStream":
            return self

        async def __anext__(self) -> bytes:
            raise RuntimeError("boom")

    proc = FakeBuildProcess([], returncode=0)
    proc.stdout = ExplodingLineStream()

    async def fake_exec(*_args, **_kwargs):
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    registry = ImageBuildRegistry()
    build = ImageBuild(id="b4", kind="project", project_name="web", image_tag="coderfleet-web:latest")

    asyncio.run(run_image_build(
        build, builds_dir, tmp_path / "Dockerfile", tmp_path, "linux/amd64", registry,
    ))

    saved = ImageBuild.load(builds_dir / "b4.json")
    assert saved.status == ImageBuildStatus.failed
    log_text = (builds_dir / "b4.log").read_text(encoding="utf-8")
    assert "构建出错" in log_text
    assert "boom" in log_text


def test_successful_build_wins_over_a_cancel_requested_at_the_very_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """回归测试：docker build 已经跑完并 rc==0，就算 cancel() 恰好在这最后一刻
    被调用（例如用户在构建即将完成时点了停止），也应该记成 succeeded，而不是
    succeeded 的构建被误记成 cancelled、连带漏掉 on_success 的 projects.conf 写回。"""
    builds_dir = tmp_path / "builds"
    registry = ImageBuildRegistry()

    class LateCancelLineStream:
        def __init__(self, lines: list[bytes]) -> None:
            self._lines = list(lines)

        def __aiter__(self) -> "LateCancelLineStream":
            return self

        async def __anext__(self) -> bytes:
            if not self._lines:
                # 模拟：docker build 进程实际上已经跑完，但在我们的 async-for
                # 检测到 EOF 之前，DELETE 请求先落到了 registry 上。
                await registry.cancel("b5")
                raise StopAsyncIteration
            return self._lines.pop(0)

    proc = FakeBuildProcess([b"Successfully built xyz\n"], returncode=0)
    proc.stdout = LateCancelLineStream([b"Successfully built xyz\n"])

    async def fake_exec(*_args, **_kwargs):
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    build = ImageBuild(id="b5", kind="project", project_name="web", image_tag="coderfleet-web:latest")
    on_success_calls = []

    asyncio.run(run_image_build(
        build, builds_dir, tmp_path / "Dockerfile", tmp_path, "linux/amd64", registry,
        on_success=on_success_calls.append,
    ))

    saved = ImageBuild.load(builds_dir / "b5.json")
    assert saved.status == ImageBuildStatus.succeeded
    assert on_success_calls == ["coderfleet-web:latest"]


def test_failed_build_persists_failed_status_without_on_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    builds_dir = tmp_path / "builds"
    proc = FakeBuildProcess([b"an error occurred\n"], returncode=1)

    async def fake_exec(*_args, **_kwargs):
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    registry = ImageBuildRegistry()
    build = ImageBuild(id="b2", kind="project", project_name="web", image_tag="coderfleet-web:latest")
    on_success_calls = []

    asyncio.run(run_image_build(
        build, builds_dir, tmp_path / "Dockerfile", tmp_path, "linux/amd64",
        registry, on_success=on_success_calls.append,
    ))

    saved = ImageBuild.load(builds_dir / "b2.json")
    assert saved.status == ImageBuildStatus.failed
    assert saved.exit_code == 1
    assert on_success_calls == []


def test_cancelled_build_persists_cancelled_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """模拟：构建进行到一半时，另一端调用 registry.cancel() 请求停止。

    registry.track() 一定发生在 run_image_build 进入 `async for line in proc.stdout`
    之前，所以让 fake stdout 在吐出第一行之后、结束之前调用 cancel()，能确定性地
    复现"取消请求先到，子进程随后才退出"的时序，不用靠 sleep 赌运气。"""
    builds_dir = tmp_path / "builds"
    registry = ImageBuildRegistry()

    class CancellingLineStream:
        def __init__(self, lines: list[bytes]) -> None:
            self._lines = list(lines)
            self._cancelled = False

        def __aiter__(self) -> "CancellingLineStream":
            return self

        async def __anext__(self) -> bytes:
            if not self._lines:
                raise StopAsyncIteration
            line = self._lines.pop(0)
            if not self._cancelled:
                self._cancelled = True
                await registry.cancel("b3")
            return line

    proc = FakeBuildProcess([b"building...\n"], returncode=143)
    proc.stdout = CancellingLineStream([b"building...\n"])

    async def fake_exec(*_args, **_kwargs):
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    build = ImageBuild(id="b3", kind="project", project_name="web", image_tag="coderfleet-web:latest")

    asyncio.run(run_image_build(
        build, builds_dir, tmp_path / "Dockerfile", tmp_path, "linux/amd64", registry,
    ))

    saved = ImageBuild.load(builds_dir / "b3.json")
    assert saved.status == ImageBuildStatus.cancelled
    assert proc.terminated is True  # cancel() 确实 terminate() 了子进程
    log_text = (builds_dir / "b3.log").read_text(encoding="utf-8")
    assert "已停止" in log_text
