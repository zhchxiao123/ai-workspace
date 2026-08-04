"""test_compose_local_runtime.py — RUNTIME=local 账号绑定的项目不应该生成 docker-compose 服务。

local 账号不经过 Docker；如果 compose 生成时仍然照旧给它的项目拼一个服务定义，
`coderfleet apply` 会去启动一个本不该存在的容器，跟 local runtime 的整个模型矛盾。
"""
from __future__ import annotations

from pathlib import Path

import click
import pytest

from coderfleet.compose import generate_compose


def test_generate_compose_skips_project_bound_to_local_runtime_account(tmp_path: Path) -> None:
    local_repo = tmp_path / "local-repo"
    local_repo.mkdir()
    container_repo = tmp_path / "container-repo"
    container_repo.mkdir()

    (tmp_path / "config.conf").write_text(
        "IMAGE_NAME=coderfleet\nIMAGE_TAG=latest\nBUILD_PLATFORM=linux/amd64\n",
        encoding="utf-8",
    )
    (tmp_path / "accounts.conf").write_text(
        "NAME=alice TYPE=claude AUTH=login RUNTIME=local\n"
        "NAME=bob TYPE=codex AUTH=login RUNTIME=container\n",
        encoding="utf-8",
    )
    (tmp_path / "projects.conf").write_text(
        f"NAME=local-proj ACCOUNT=alice PATH={local_repo}\n"
        f"NAME=container-proj ACCOUNT=bob PATH={container_repo}\n",
        encoding="utf-8",
    )

    compose = generate_compose(tmp_path)
    service_names = set(compose["services"].keys())

    assert "claude-project-local-proj" not in service_names
    assert "codex-project-container-proj" in service_names


def test_generate_compose_rejects_workspace_with_only_local_projects(tmp_path: Path) -> None:
    """Every remaining project is local-only → generate_compose already has an existing
    `count == 0` guard (compose.py) that rejects this explicitly rather than silently
    emitting a compose file with zero project services — this test just pins that the
    local-runtime skip feeds into that same, already-correct guard."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (tmp_path / "config.conf").write_text(
        "IMAGE_NAME=coderfleet\nIMAGE_TAG=latest\nBUILD_PLATFORM=linux/amd64\n",
        encoding="utf-8",
    )
    (tmp_path / "accounts.conf").write_text(
        "NAME=alice TYPE=claude AUTH=login RUNTIME=local\n", encoding="utf-8",
    )
    (tmp_path / "projects.conf").write_text(
        f"NAME=local-proj ACCOUNT=alice PATH={repo}\n", encoding="utf-8",
    )

    with pytest.raises(click.ClickException):
        generate_compose(tmp_path)
