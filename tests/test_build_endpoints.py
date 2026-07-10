"""test_build_endpoints.py — /api/builds* 端点与「断线不中断构建」行为。"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from coderfleet.server import main as server_main
from coderfleet.server.models import ImageBuild, ImageBuildStatus
from coderfleet.server.scheduler import Scheduler


def _make_workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "config.conf").write_text(
        "IMAGE_NAME=coderfleet\nIMAGE_TAG=latest\nBUILD_PLATFORM=linux/amd64\n", encoding="utf-8",
    )
    (ws / "repo").mkdir()
    (ws / "accounts.conf").write_text("NAME=api-claude TYPE=claude AUTH=login\n", encoding="utf-8")
    (ws / "projects.conf").write_text(
        f"NAME=web ACCOUNT=api-claude PATH={ws / 'repo'}\n", encoding="utf-8",
    )
    return ws


def _use_scheduler(monkeypatch: pytest.MonkeyPatch, ws: Path) -> None:
    monkeypatch.setattr(server_main, "scheduler", Scheduler(ws))
    monkeypatch.setattr(server_main, "WORKSPACE_DIR", ws)


class _FakeLineStream:
    def __init__(self, lines: list[bytes]) -> None:
        self._lines = list(lines)

    def __aiter__(self) -> "_FakeLineStream":
        return self

    async def __anext__(self) -> bytes:
        await asyncio.sleep(0)
        if not self._lines:
            raise StopAsyncIteration
        return self._lines.pop(0)


class _FakeBuildProcess:
    def __init__(self, lines: list[bytes], returncode: int = 0) -> None:
        self.stdout = _FakeLineStream(lines)
        self._rc = returncode
        self.returncode = None

    def terminate(self) -> None:
        pass

    def kill(self) -> None:
        pass

    async def wait(self) -> int:
        self.returncode = self._rc
        return self._rc


async def _drain(resp) -> str:
    chunks = []
    async for chunk in resp.body_iterator:
        chunks.append(chunk)
    return "".join(chunks)


def test_build_project_image_404_for_missing_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = _make_workspace(tmp_path)
    _use_scheduler(monkeypatch, ws)

    with pytest.raises(Exception) as exc_info:
        asyncio.run(server_main.build_project_image("ghost", None))
    assert "404" in str(exc_info.value) or "不存在" in str(exc_info.value)


def test_build_project_image_persists_record_and_log_and_survives_disconnect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = _make_workspace(tmp_path)
    _use_scheduler(monkeypatch, ws)
    (ws / "projects" / "web").mkdir(parents=True)
    (ws / "projects" / "web" / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")

    proc = _FakeBuildProcess([b"Step 1/1\n", b"Successfully built abc\n"], returncode=0)

    async def fake_exec(*_args, **_kwargs):
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    req = server_main.ImageBuildRequest(image_tag="coderfleet-web:latest", build_id="b1")

    async def scenario():
        resp = await server_main.build_project_image("web", req)
        # 模拟浏览器立刻断开：不去读 resp 的内容，只是不再消费它。
        # 构建本身由 asyncio.create_task 驱动，不依赖这次响应是否被读取。
        # 这里显式 await 一次事件循环，让后台任务有机会推进到完成。
        for _ in range(50):
            await asyncio.sleep(0)
            build = server_main.scheduler.get_build("b1")
            if build is not None and build.status != ImageBuildStatus.running:
                break
        return resp

    asyncio.run(scenario())

    saved = server_main.scheduler.get_build("b1")
    assert saved is not None
    assert saved.status == ImageBuildStatus.succeeded
    log_text = server_main.scheduler.get_build_log_path("b1").read_text(encoding="utf-8")
    assert "Step 1/1" in log_text
    assert "构建完成" in log_text

    # 项目的 image 应该已经写回 projects.conf
    project = next(p for p in server_main.scheduler.get_projects() if p.name == "web")
    assert project.image == "coderfleet-web:latest"


def test_list_and_get_build_endpoints(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = _make_workspace(tmp_path)
    _use_scheduler(monkeypatch, ws)

    ImageBuild(id="b1", kind="project", project_name="web", image_tag="a:latest").save(
        server_main.scheduler.builds_dir
    )
    ImageBuild(id="b2", kind="project", project_name="other", image_tag="b:latest").save(
        server_main.scheduler.builds_dir
    )

    result = asyncio.run(server_main.list_builds(project="web", limit=50))
    assert {b.id for b in result} == {"b1"}

    got = asyncio.run(server_main.get_build("b1"))
    assert got.id == "b1"

    with pytest.raises(Exception):
        asyncio.run(server_main.get_build("missing"))


def test_get_build_logs_returns_full_text(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = _make_workspace(tmp_path)
    _use_scheduler(monkeypatch, ws)
    server_main.scheduler.builds_dir.mkdir(parents=True, exist_ok=True)
    server_main.scheduler.get_build_log_path("b1").write_text("hello build\n", encoding="utf-8")

    text = asyncio.run(server_main.get_build_logs("b1"))
    assert text == "hello build\n"

    with pytest.raises(Exception):
        asyncio.run(server_main.get_build_logs("missing"))


def test_build_shared_image_requires_shared_dockerfile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = _make_workspace(tmp_path)
    _use_scheduler(monkeypatch, ws)

    with pytest.raises(Exception) as exc_info:
        asyncio.run(server_main.build_shared_image(None))
    assert "Dockerfile" in str(exc_info.value)


def test_build_shared_image_persists_record_with_shared_kind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = _make_workspace(tmp_path)
    _use_scheduler(monkeypatch, ws)
    (ws / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")

    proc = _FakeBuildProcess([b"Successfully built xyz\n"], returncode=0)

    async def fake_exec(*_args, **_kwargs):
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    req = server_main.SharedImageBuildRequest(build_id="shared1")

    async def scenario():
        await server_main.build_shared_image(req)
        for _ in range(50):
            await asyncio.sleep(0)
            build = server_main.scheduler.get_build("shared1")
            if build is not None and build.status != ImageBuildStatus.running:
                break

    asyncio.run(scenario())

    saved = server_main.scheduler.get_build("shared1")
    assert saved is not None
    assert saved.kind == "shared"
    assert saved.project_name == ""
    assert saved.status == ImageBuildStatus.succeeded
    assert saved.image_tag == "coderfleet:latest"


def test_list_builds_filters_by_kind(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = _make_workspace(tmp_path)
    _use_scheduler(monkeypatch, ws)

    ImageBuild(id="b1", kind="project", project_name="web", image_tag="a:latest").save(
        server_main.scheduler.builds_dir
    )
    ImageBuild(id="b2", kind="shared", project_name="", image_tag="coderfleet:latest").save(
        server_main.scheduler.builds_dir
    )

    result = asyncio.run(server_main.list_builds(kind="shared", limit=50))
    assert {b.id for b in result} == {"b2"}


def test_cancel_shared_image_build_reports_not_found_when_untracked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = _make_workspace(tmp_path)
    _use_scheduler(monkeypatch, ws)

    result = asyncio.run(server_main.cancel_shared_image_build("missing"))
    assert result == {"ok": False, "message": "没有正在构建的镜像"}
