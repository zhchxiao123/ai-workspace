from __future__ import annotations

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def make_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "config.conf").write_text(
        "IMAGE_NAME=coderfleet\nIMAGE_TAG=latest\nBUILD_PLATFORM=linux/amd64\n",
        encoding="utf-8",
    )
    (workspace / "repo").mkdir()
    return workspace


def fake_docker_path(tmp_path: Path) -> str:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(
        "#!/usr/bin/env bash\n"
        "if [[ \"$1\" == \"compose\" && \"$2\" == \"version\" ]]; then exit 0; fi\n"
        "if [[ \"$1\" == \"compose\" ]]; then exit 0; fi\n"
        "if [[ \"$1\" == \"image\" && \"$2\" == \"inspect\" ]]; then exit 0; fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    return f"{bin_dir}{os.pathsep}{os.environ['PATH']}"


def run_coderfleet(workspace: Path, path: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["coderfleet", *args],
        cwd=workspace,
        env={**os.environ, "PATH": path, "CODERFLEET_WORKSPACE": str(workspace)},
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def test_apply_injects_env_file_for_claude_env_account(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    (workspace / "accounts.conf").write_text(
        "NAME=api-claude TYPE=claude AUTH=env ENV_FILE=./accounts/api-claude/env\n",
        encoding="utf-8",
    )
    (workspace / "projects.conf").write_text(
        f"NAME=repo ACCOUNT=api-claude PATH={workspace / 'repo'}\n",
        encoding="utf-8",
    )

    result = run_coderfleet(workspace, fake_docker_path(tmp_path), "apply")

    assert result.returncode == 0, result.stderr
    compose = (workspace / "docker-compose.yml").read_text(encoding="utf-8")
    assert "CODERFLEET_ACCOUNT_AUTH: env" in compose
    assert "env_file:" in compose
    assert "- ./accounts/api-claude/env" in compose


def test_apply_mounts_opencode_auth_dir(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    (workspace / "accounts.conf").write_text(
        "NAME=api-opencode TYPE=opencode AUTH=env ENV_FILE=./accounts/api-opencode/env\n",
        encoding="utf-8",
    )
    (workspace / "projects.conf").write_text(
        f"NAME=repo ACCOUNT=api-opencode PATH={workspace / 'repo'}\n",
        encoding="utf-8",
    )

    result = run_coderfleet(workspace, fake_docker_path(tmp_path), "apply")

    assert result.returncode == 0, result.stderr
    compose = (workspace / "docker-compose.yml").read_text(encoding="utf-8")
    assert "opencode-project-repo:" in compose
    assert "container_name: opencode-repo" in compose
    assert "./accounts/api-opencode:/home/byclaw/.opencode" in compose
    assert "XDG_DATA_HOME: /home/byclaw/.opencode/data" in compose
    assert "XDG_CONFIG_HOME: /home/byclaw/.opencode/config" in compose
    assert "XDG_STATE_HOME: /home/byclaw/.opencode/state" in compose
    assert "XDG_CACHE_HOME: /home/byclaw/.opencode/cache" in compose
    assert "- ./accounts/api-opencode/env" in compose


def test_apply_disables_proxy_for_account_proxy_off(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    (workspace / "accounts.conf").write_text(
        "NAME=api-claude TYPE=claude AUTH=env PROXY=off\n",
        encoding="utf-8",
    )
    (workspace / "projects.conf").write_text(
        f"NAME=repo ACCOUNT=api-claude PATH={workspace / 'repo'}\n",
        encoding="utf-8",
    )

    result = run_coderfleet(workspace, fake_docker_path(tmp_path), "apply")

    assert result.returncode == 0, result.stderr
    compose = (workspace / "docker-compose.yml").read_text(encoding="utf-8")
    service = compose.split("  claude-project-repo:", 1)[1]
    assert "extnet: {}" in service
    assert "intnet: {}" not in service
    assert "HTTP_PROXY" not in service
    assert "depends_on:" not in service
    assert 'CODERFLEET_ACCOUNT_PROXY: "off"' in service


def test_account_add_accepts_proxy_off(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    (workspace / "accounts.conf").write_text("", encoding="utf-8")
    (workspace / "projects.conf").write_text("", encoding="utf-8")

    result = run_coderfleet(
        workspace,
        fake_docker_path(tmp_path),
        "account",
        "add",
        "api-claude",
        "TYPE=claude",
        "--auth",
        "env",
        "--proxy",
        "off",
    )

    assert result.returncode == 0, result.stderr
    accounts_conf = (workspace / "accounts.conf").read_text(encoding="utf-8")
    assert "PROXY=off" in accounts_conf


def test_project_add_rejects_proxy_option(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    (workspace / "accounts.conf").write_text(
        "NAME=api-claude TYPE=claude AUTH=env PROXY=off\n",
        encoding="utf-8",
    )
    (workspace / "projects.conf").write_text("", encoding="utf-8")

    result = run_coderfleet(
        workspace,
        fake_docker_path(tmp_path),
        "project",
        "add",
        "repo",
        "api-claude",
        str(workspace / "repo"),
        "PROXY=off",
    )

    assert result.returncode != 0
    assert "unexpected extra argument" in result.stderr


def test_login_skips_claude_env_account(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    (workspace / "accounts.conf").write_text(
        "NAME=api-claude TYPE=claude AUTH=env ENV_FILE=./accounts/api-claude/env\n",
        encoding="utf-8",
    )
    (workspace / "projects.conf").write_text("", encoding="utf-8")

    result = run_coderfleet(workspace, fake_docker_path(tmp_path), "login", "api-claude")

    assert result.returncode == 0, result.stderr
    assert "使用环境变量认证" in result.stdout
    assert "无需交互登录" in result.stdout


def test_account_add_env_defaults_env_file(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    (workspace / "accounts.conf").write_text("", encoding="utf-8")
    (workspace / "projects.conf").write_text("", encoding="utf-8")

    result = run_coderfleet(
        workspace,
        fake_docker_path(tmp_path),
        "account",
        "add",
        "api-claude",
        "TYPE=claude",
        "--auth",
        "env",
    )

    assert result.returncode == 0, result.stderr
    accounts_conf = (workspace / "accounts.conf").read_text(encoding="utf-8")
    assert "ENV_FILE=./accounts/api-claude/env" in accounts_conf
    # env file path is printed separately; hint text comes from the account type registry
    assert "./accounts/api-claude/env" in result.stdout


def test_account_add_accepts_opencode_env_account(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    (workspace / "accounts.conf").write_text("", encoding="utf-8")
    (workspace / "projects.conf").write_text("", encoding="utf-8")

    result = run_coderfleet(
        workspace,
        fake_docker_path(tmp_path),
        "account",
        "add",
        "api-opencode",
        "TYPE=opencode",
        "--auth",
        "env",
    )

    assert result.returncode == 0, result.stderr
    accounts_conf = (workspace / "accounts.conf").read_text(encoding="utf-8")
    assert "TYPE=opencode" in accounts_conf
    assert "ENV_FILE=./accounts/api-opencode/env" in accounts_conf
