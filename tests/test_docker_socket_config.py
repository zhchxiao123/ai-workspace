from __future__ import annotations

from pathlib import Path

from coderfleet.compose import generate_compose
from coderfleet.docker_socket import resolve_docker_socket
from coderfleet.server.models import Project, ProjectResponse
from coderfleet.server.scheduler import Scheduler
from coderfleet.server.settings_schema import field_for


def _write_minimal_workspace(ws: Path, project_path: Path) -> None:
    (ws / "accounts.conf").write_text("NAME=api-codex TYPE=codex AUTH=login\n", encoding="utf-8")
    (ws / "projects.conf").write_text(
        f"NAME=repo ACCOUNT=api-codex PATH={project_path}\n",
        encoding="utf-8",
    )


def test_resolve_docker_socket_accepts_colima_unix_endpoint(tmp_path: Path) -> None:
    socket_path = tmp_path / ".colima" / "default" / "docker.sock"
    socket_path.parent.mkdir(parents=True)
    socket_path.touch()

    resolved = resolve_docker_socket(
        {"DOCKER_SOCKET": "auto"},
        docker_host=f"unix://{socket_path}",
        context_host="",
    )

    assert resolved is not None
    assert resolved.host_path == str(socket_path)
    assert resolved.container_path == "/var/run/docker.sock"
    assert resolved.env == "unix:///var/run/docker.sock"


def test_generate_compose_mounts_configured_docker_socket(tmp_path: Path) -> None:
    project_path = tmp_path / "repo"
    project_path.mkdir()
    socket_path = tmp_path / "docker.sock"
    socket_path.touch()
    _write_minimal_workspace(tmp_path, project_path)
    (tmp_path / "config.conf").write_text(
        "\n".join([
            "IMAGE_NAME=coderfleet",
            "IMAGE_TAG=latest",
            "BUILD_PLATFORM=linux/amd64",
            f"DOCKER_SOCKET={socket_path}",
        ]),
        encoding="utf-8",
    )

    compose = generate_compose(tmp_path)
    service = compose["services"]["codex-project-repo"]

    assert f"{socket_path}:/var/run/docker.sock" in service["volumes"]
    assert service["environment"]["DOCKER_HOST"] == "unix:///var/run/docker.sock"
    assert service["environment"]["CODERFLEET_HOST_WORKSPACE"] == str(project_path)


def test_project_docker_socket_off_overrides_global_socket(tmp_path: Path) -> None:
    project_path = tmp_path / "repo"
    project_path.mkdir()
    socket_path = tmp_path / "docker.sock"
    socket_path.touch()
    (tmp_path / "accounts.conf").write_text("NAME=api-codex TYPE=codex AUTH=login\n", encoding="utf-8")
    (tmp_path / "projects.conf").write_text(
        f"NAME=repo ACCOUNT=api-codex PATH={project_path} DOCKER_SOCKET=off\n",
        encoding="utf-8",
    )
    (tmp_path / "config.conf").write_text(f"DOCKER_SOCKET={socket_path}\n", encoding="utf-8")

    compose = generate_compose(tmp_path)
    service = compose["services"]["codex-project-repo"]

    assert not any(str(socket_path) in volume for volume in service["volumes"])
    assert "DOCKER_HOST" not in service["environment"]


def test_project_docker_socket_path_overrides_global_socket(tmp_path: Path) -> None:
    project_path = tmp_path / "repo"
    project_path.mkdir()
    global_socket = tmp_path / "global.sock"
    project_socket = tmp_path / "project.sock"
    global_socket.touch()
    project_socket.touch()
    (tmp_path / "accounts.conf").write_text("NAME=api-codex TYPE=codex AUTH=login\n", encoding="utf-8")
    (tmp_path / "projects.conf").write_text(
        f"NAME=repo ACCOUNT=api-codex PATH={project_path} DOCKER_SOCKET={project_socket}\n",
        encoding="utf-8",
    )
    (tmp_path / "config.conf").write_text(f"DOCKER_SOCKET={global_socket}\n", encoding="utf-8")

    compose = generate_compose(tmp_path)
    service = compose["services"]["codex-project-repo"]

    assert f"{project_socket}:/var/run/docker.sock" in service["volumes"]
    assert not any(str(global_socket) in volume for volume in service["volumes"])


def test_docker_socket_settings_are_exposed_in_ui_schema() -> None:
    socket_field = field_for("DOCKER_SOCKET")
    target_field = field_for("DOCKER_SOCKET_TARGET")

    assert socket_field is not None
    assert socket_field.requires_apply
    assert target_field is not None
    assert target_field.placeholder == "/var/run/docker.sock"


def test_project_response_exposes_project_docker_socket() -> None:
    response = ProjectResponse.from_project(
        Project(name="repo", account="alice", path="/repo", docker_socket="auto")
    )

    assert response.docker_socket == "auto"


def test_scheduler_save_project_persists_project_docker_socket(tmp_path: Path) -> None:
    sched = Scheduler(tmp_path)

    project = sched.save_project("repo", "alice", "/repo", docker_socket="auto")

    assert project.docker_socket == "auto"
    assert "DOCKER_SOCKET=auto" in (tmp_path / "projects.conf").read_text(encoding="utf-8")
