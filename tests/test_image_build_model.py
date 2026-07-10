"""test_image_build_model.py — ImageBuild 持久化 + Scheduler 查询 helper。"""
from __future__ import annotations

from pathlib import Path

from coderfleet.server.models import ImageBuild, ImageBuildStatus
from coderfleet.server.scheduler import Scheduler


def test_image_build_save_load_roundtrip(tmp_path: Path) -> None:
    builds_dir = tmp_path / "builds"
    build = ImageBuild(id="b1", kind="project", project_name="web", image_tag="coderfleet-web:latest")
    build.save(builds_dir)

    loaded = ImageBuild.load(builds_dir / "b1.json")
    assert loaded.id == "b1"
    assert loaded.status == ImageBuildStatus.running
    assert loaded.finished is None

    loaded.update_status(ImageBuildStatus.succeeded, builds_dir, exit_code=0)
    reloaded = ImageBuild.load(builds_dir / "b1.json")
    assert reloaded.status == ImageBuildStatus.succeeded
    assert reloaded.exit_code == 0
    assert reloaded.finished is not None


def test_scheduler_list_builds_filters_by_project(tmp_path: Path) -> None:
    ws = tmp_path / "workspace"
    ws.mkdir()
    sched = Scheduler(ws)

    ImageBuild(id="b1", kind="project", project_name="web", image_tag="a:latest").save(sched.builds_dir)
    ImageBuild(id="b2", kind="project", project_name="api", image_tag="b:latest").save(sched.builds_dir)
    ImageBuild(id="b3", kind="shared", project_name="", image_tag="c:latest").save(sched.builds_dir)

    assert {b.id for b in sched.list_builds()} == {"b1", "b2", "b3"}
    assert {b.id for b in sched.list_builds(project_name="web")} == {"b1"}
    assert sched.get_build("b1") is not None
    assert sched.get_build("missing") is None
    assert sched.get_build_log_path("b1") == sched.builds_dir / "b1.log"
