from __future__ import annotations

import json
import os
import re
import socket
import subprocess
from pathlib import Path

import yaml


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


def fake_docker_path(tmp_path: Path, call_log: Path | None = None) -> str:
    """Create a stub `docker` binary. If call_log is given, every invocation's
    arguments are appended to it (one line each) so tests can assert on
    exactly which compose/docker commands were run."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    docker = bin_dir / "docker"
    log_line = f'echo "$@" >> "{call_log}"\n' if call_log is not None else ""
    docker.write_text(
        "#!/usr/bin/env bash\n"
        f"{log_line}"
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
        env={
            **os.environ,
            "PATH": path,
            "PYTHONPATH": str(ROOT),
            "CODERFLEET_WORKSPACE": str(workspace),
        },
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def make_two_project_workspace(tmp_path: Path) -> Path:
    workspace = make_workspace(tmp_path)
    (workspace / "accounts.conf").write_text(
        "NAME=api-claude TYPE=claude AUTH=login\n",
        encoding="utf-8",
    )
    (workspace / "repo2").mkdir()
    (workspace / "projects.conf").write_text(
        f"NAME=repo ACCOUNT=api-claude PATH={workspace / 'repo'}\n"
        f"NAME=repo2 ACCOUNT=api-claude PATH={workspace / 'repo2'}\n",
        encoding="utf-8",
    )
    return workspace


def test_up_with_no_args_does_not_force_recreate(tmp_path: Path) -> None:
    workspace = make_two_project_workspace(tmp_path)
    call_log = tmp_path / "docker-calls.log"

    result = run_coderfleet(workspace, fake_docker_path(tmp_path, call_log), "up")

    assert result.returncode == 0, result.stderr
    calls = call_log.read_text(encoding="utf-8")
    assert "--force-recreate" not in calls
    assert "up -d" in calls


def test_up_scoped_regenerates_compose_for_a_project_added_after_last_apply(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    (workspace / "accounts.conf").write_text(
        "NAME=api-claude TYPE=claude AUTH=login\n",
        encoding="utf-8",
    )
    (workspace / "projects.conf").write_text(
        f"NAME=repo ACCOUNT=api-claude PATH={workspace / 'repo'}\n",
        encoding="utf-8",
    )
    docker_path = fake_docker_path(tmp_path)
    assert run_coderfleet(workspace, docker_path, "apply").returncode == 0

    # simulate `project add` for a brand-new project, without re-running apply
    (workspace / "repo2").mkdir()
    with (workspace / "projects.conf").open("a", encoding="utf-8") as f:
        f.write(f"NAME=repo2 ACCOUNT=api-claude PATH={workspace / 'repo2'}\n")

    result = run_coderfleet(workspace, docker_path, "up", "repo2")

    assert result.returncode == 0, result.stderr
    compose = (workspace / "docker-compose.yml").read_text(encoding="utf-8")
    assert "claude-project-repo2" in compose


def test_up_scoped_to_one_project_only_references_that_service(tmp_path: Path) -> None:
    workspace = make_two_project_workspace(tmp_path)
    call_log = tmp_path / "docker-calls.log"

    result = run_coderfleet(workspace, fake_docker_path(tmp_path, call_log), "up", "repo")

    assert result.returncode == 0, result.stderr
    calls = call_log.read_text(encoding="utf-8")
    up_lines = [line for line in calls.splitlines() if line.startswith("compose") and " up " in f" {line} "]
    assert up_lines, calls
    assert "claude-project-repo" in up_lines[-1]
    assert "claude-project-repo2" not in up_lines[-1]


def test_up_scoped_to_unknown_project_fails(tmp_path: Path) -> None:
    workspace = make_two_project_workspace(tmp_path)

    result = run_coderfleet(workspace, fake_docker_path(tmp_path), "up", "does-not-exist")

    assert result.returncode != 0
    assert "does-not-exist" in result.stderr


def test_up_scoped_start_is_same_command_whether_new_or_previously_stopped(tmp_path: Path) -> None:
    workspace = make_two_project_workspace(tmp_path)
    call_log = tmp_path / "docker-calls.log"
    docker_path = fake_docker_path(tmp_path, call_log)

    first = run_coderfleet(workspace, docker_path, "up", "repo")
    run_coderfleet(workspace, docker_path, "down", "repo")
    second = run_coderfleet(workspace, docker_path, "up", "repo")

    assert first.returncode == 0 and second.returncode == 0
    up_lines = [line for line in call_log.read_text(encoding="utf-8").splitlines() if " up " in f" {line} "]
    assert len(up_lines) == 2
    assert up_lines[0] == up_lines[1]


def _command_tokens(calls_text: str) -> list[list[str]]:
    """Split each recorded docker invocation into whitespace tokens, so
    assertions can check for exact subcommands (e.g. "down") without false
    positives from tmp_path components that happen to contain the same
    substring (e.g. a test named test_down_... whose tmp dir path contains
    "down")."""
    return [line.split() for line in calls_text.splitlines() if line.strip()]


def test_down_with_no_args_keeps_full_teardown(tmp_path: Path) -> None:
    workspace = make_two_project_workspace(tmp_path)
    call_log = tmp_path / "docker-calls.log"

    result = run_coderfleet(workspace, fake_docker_path(tmp_path, call_log), "down")

    assert result.returncode == 0, result.stderr
    lines = _command_tokens(call_log.read_text(encoding="utf-8"))
    assert any(tokens[-1] == "down" for tokens in lines)
    assert not any("stop" in tokens for tokens in lines)


def test_down_scoped_to_one_project_uses_non_destructive_stop(tmp_path: Path) -> None:
    workspace = make_two_project_workspace(tmp_path)
    call_log = tmp_path / "docker-calls.log"

    result = run_coderfleet(workspace, fake_docker_path(tmp_path, call_log), "down", "repo")

    assert result.returncode == 0, result.stderr
    lines = _command_tokens(call_log.read_text(encoding="utf-8"))
    assert not any("down" in tokens for tokens in lines)
    stop_lines = [tokens for tokens in lines if "stop" in tokens]
    assert stop_lines
    assert "claude-project-repo" in stop_lines[-1]
    assert "claude-project-repo2" not in stop_lines[-1]


def test_restart_scoped_to_one_project_stops_then_starts_only_that_service(tmp_path: Path) -> None:
    workspace = make_two_project_workspace(tmp_path)
    call_log = tmp_path / "docker-calls.log"

    result = run_coderfleet(workspace, fake_docker_path(tmp_path, call_log), "restart", "repo")

    assert result.returncode == 0, result.stderr
    calls = call_log.read_text(encoding="utf-8").splitlines()
    stop_lines = [line for line in calls if " stop " in f" {line} "]
    up_lines = [line for line in calls if " up " in f" {line} "]
    assert stop_lines and up_lines
    assert "claude-project-repo" in stop_lines[-1] and "claude-project-repo2" not in stop_lines[-1]
    assert "claude-project-repo" in up_lines[-1] and "claude-project-repo2" not in up_lines[-1]
    assert "--force-recreate" not in "\n".join(calls)


def test_restart_scoped_to_one_project_with_ide_includes_ide_service(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    (workspace / "accounts.conf").write_text(
        "NAME=api-claude TYPE=claude AUTH=login\n",
        encoding="utf-8",
    )
    (workspace / "projects.conf").write_text(
        f"NAME=repo ACCOUNT=api-claude PATH={workspace / 'repo'} IDE=on IDE_PORT=18080\n",
        encoding="utf-8",
    )
    call_log = tmp_path / "docker-calls.log"

    result = run_coderfleet(workspace, fake_docker_path(tmp_path, call_log), "restart", "repo")

    assert result.returncode == 0, result.stderr
    calls = call_log.read_text(encoding="utf-8").splitlines()
    stop_lines = [line for line in calls if " stop " in f" {line} "]
    up_lines = [line for line in calls if " up " in f" {line} "]
    assert stop_lines and up_lines
    assert "claude-project-repo" in stop_lines[-1] and "ide-project-repo" in stop_lines[-1]
    assert "claude-project-repo" in up_lines[-1] and "ide-project-repo" in up_lines[-1]


def test_apply_default_is_incremental(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    (workspace / "accounts.conf").write_text(
        "NAME=api-claude TYPE=claude AUTH=login\n",
        encoding="utf-8",
    )
    (workspace / "projects.conf").write_text(
        f"NAME=repo ACCOUNT=api-claude PATH={workspace / 'repo'}\n",
        encoding="utf-8",
    )
    call_log = tmp_path / "docker-calls.log"

    result = run_coderfleet(workspace, fake_docker_path(tmp_path, call_log), "apply")

    assert result.returncode == 0, result.stderr
    calls = call_log.read_text(encoding="utf-8")
    assert "down" not in calls
    assert "--force-recreate" not in calls
    assert "up -d --remove-orphans" in calls


def test_apply_full_tears_down_and_force_recreates(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    (workspace / "accounts.conf").write_text(
        "NAME=api-claude TYPE=claude AUTH=login\n",
        encoding="utf-8",
    )
    (workspace / "projects.conf").write_text(
        f"NAME=repo ACCOUNT=api-claude PATH={workspace / 'repo'}\n",
        encoding="utf-8",
    )
    call_log = tmp_path / "docker-calls.log"

    result = run_coderfleet(workspace, fake_docker_path(tmp_path, call_log), "apply", "--full")

    assert result.returncode == 0, result.stderr
    calls = call_log.read_text(encoding="utf-8")
    assert "down --remove-orphans" in calls
    assert "up -d --force-recreate" in calls


def test_apply_full_prints_warning(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    (workspace / "accounts.conf").write_text(
        "NAME=api-claude TYPE=claude AUTH=login\n",
        encoding="utf-8",
    )
    (workspace / "projects.conf").write_text(
        f"NAME=repo ACCOUNT=api-claude PATH={workspace / 'repo'}\n",
        encoding="utf-8",
    )

    result = run_coderfleet(workspace, fake_docker_path(tmp_path), "apply", "--full")

    assert result.returncode == 0, result.stderr
    assert "中断" in result.stdout


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


def test_apply_mounts_kimi_auth_dir(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    (workspace / "accounts.conf").write_text(
        "NAME=api-kimi TYPE=kimi AUTH=env ENV_FILE=./accounts/api-kimi/env\n",
        encoding="utf-8",
    )
    (workspace / "projects.conf").write_text(
        f"NAME=repo ACCOUNT=api-kimi PATH={workspace / 'repo'}\n",
        encoding="utf-8",
    )

    result = run_coderfleet(workspace, fake_docker_path(tmp_path), "apply")

    assert result.returncode == 0, result.stderr
    compose = (workspace / "docker-compose.yml").read_text(encoding="utf-8")
    assert "kimi-project-repo:" in compose
    assert "container_name: kimi-repo" in compose
    assert "./accounts/api-kimi:/home/byclaw/.kimi-code" in compose
    assert "KIMI_CODE_HOME: /home/byclaw/.kimi-code" in compose
    assert "KIMI_CODE_NO_AUTO_UPDATE: '1'" in compose
    assert "KIMI_DISABLE_TELEMETRY: '1'" in compose
    assert "- ./accounts/api-kimi/env" in compose


def test_apply_enables_project_ide(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    (workspace / "accounts.conf").write_text(
        "NAME=api-codex TYPE=codex AUTH=login\n",
        encoding="utf-8",
    )
    (workspace / "projects.conf").write_text(
        f"NAME=repo ACCOUNT=api-codex PATH={workspace / 'repo'} IDE=on IDE_PORT=18080\n",
        encoding="utf-8",
    )

    result = run_coderfleet(workspace, fake_docker_path(tmp_path), "apply")

    assert result.returncode == 0, result.stderr
    compose = (workspace / "docker-compose.yml").read_text(encoding="utf-8")
    assert "CODERFLEET_IDE: \"on\"" in compose
    assert "CODERFLEET_IDE_PORT: '8080'" in compose
    assert "CODERFLEET_IDE_AUTH: none" in compose
    assert "ide-project-repo:" in compose
    assert "container_name: coderfleet-ide-repo" in compose
    assert "entrypoint:" in compose
    assert "- socat" in compose
    assert "- TCP:codex-project-repo:8080" in compose
    assert "ports:" in compose
    assert "- 127.0.0.1:18080:8080" in compose


def test_apply_auto_assigns_project_ide_port(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    (workspace / "accounts.conf").write_text(
        "NAME=api-codex TYPE=codex AUTH=login\n",
        encoding="utf-8",
    )
    (workspace / "projects.conf").write_text(
        f"NAME=repo ACCOUNT=api-codex PATH={workspace / 'repo'} IDE=on\n",
        encoding="utf-8",
    )

    result = run_coderfleet(workspace, fake_docker_path(tmp_path), "apply")

    assert result.returncode == 0, result.stderr
    compose = (workspace / "docker-compose.yml").read_text(encoding="utf-8")
    assert re.search(r"- 127\.0\.0\.1:\d+:8080", compose)


def test_auto_ide_port_skips_occupied_port(tmp_path: Path) -> None:
    from coderfleet.ports import allocate_ide_port

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        occupied_port = sock.getsockname()[1]

        port = allocate_ide_port([], min_port=occupied_port, max_port=occupied_port + 1)

    assert port == occupied_port + 1


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


def test_apply_configures_xray_proxy_service(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    xray_config = workspace / "xray" / "config.json"
    xray_config.parent.mkdir()
    xray_config.write_text("{}", encoding="utf-8")
    (workspace / "config.conf").write_text(
        "\n".join([
            "IMAGE_NAME=coderfleet",
            "IMAGE_TAG=latest",
            "BUILD_PLATFORM=linux/amd64",
            "PROXY_MODE=xray",
            "XRAY_IP=172.21.0.3",
            "XRAY_LISTEN_PORT=10809",
            f"XRAY_CONFIG={xray_config}",
            "RELAY_LISTEN_PORT=10808",
        ]),
        encoding="utf-8",
    )
    (workspace / "accounts.conf").write_text(
        "NAME=api-codex TYPE=codex AUTH=login\n",
        encoding="utf-8",
    )
    (workspace / "projects.conf").write_text(
        f"NAME=repo ACCOUNT=api-codex PATH={workspace / 'repo'}\n",
        encoding="utf-8",
    )

    result = run_coderfleet(workspace, fake_docker_path(tmp_path), "apply")

    assert result.returncode == 0, result.stderr
    compose = yaml.safe_load((workspace / "docker-compose.yml").read_text(encoding="utf-8"))
    xray = compose["services"]["xray-proxy"]
    relay = compose["services"]["proxy-relay"]
    assert xray["command"] == ["run", "-c", "/etc/xray/config.json"]
    assert "healthcheck" not in xray
    assert relay["command"] == "-C /etc/gost/config.yaml"
    assert relay["volumes"] == ["./proxy-relay-config.yaml:/etc/gost/config.yaml:ro"]
    assert relay["depends_on"] == {"xray-proxy": {"condition": "service_started"}}

    gost_cfg = yaml.safe_load((workspace / "proxy-relay-config.yaml").read_text(encoding="utf-8"))
    http_node = gost_cfg["chains"][0]["hops"][0]["nodes"][0]
    assert http_node["addr"] == "172.21.0.3:10809"
    dns_service = next(s for s in gost_cfg["services"] if s["name"] == "service-dns")
    assert dns_service["handler"] == {"type": "dns", "chain": "chain-0"}


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


def test_project_add_accepts_project_docker_socket(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    (workspace / "accounts.conf").write_text(
        "NAME=api-codex TYPE=codex AUTH=login\n",
        encoding="utf-8",
    )
    (workspace / "projects.conf").write_text("", encoding="utf-8")

    result = run_coderfleet(
        workspace,
        fake_docker_path(tmp_path),
        "project",
        "add",
        "repo",
        "api-codex",
        str(workspace / "repo"),
        "--docker-socket",
        "auto",
    )

    assert result.returncode == 0, result.stderr
    projects_conf = (workspace / "projects.conf").read_text(encoding="utf-8")
    assert "DOCKER_SOCKET=auto" in projects_conf


def test_project_set_docker_socket_updates_existing_project(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    (workspace / "accounts.conf").write_text(
        "NAME=api-codex TYPE=codex AUTH=login\n",
        encoding="utf-8",
    )
    (workspace / "projects.conf").write_text(
        f"NAME=repo ACCOUNT=api-codex PATH={workspace / 'repo'}\n",
        encoding="utf-8",
    )

    result = run_coderfleet(
        workspace,
        fake_docker_path(tmp_path),
        "project",
        "set-docker-socket",
        "repo",
        "off",
    )

    assert result.returncode == 0, result.stderr
    projects_conf = (workspace / "projects.conf").read_text(encoding="utf-8")
    assert "DOCKER_SOCKET=off" in projects_conf


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


def test_account_add_accepts_kimi_env_account(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    (workspace / "accounts.conf").write_text("", encoding="utf-8")
    (workspace / "projects.conf").write_text("", encoding="utf-8")

    result = run_coderfleet(
        workspace,
        fake_docker_path(tmp_path),
        "account",
        "add",
        "api-kimi",
        "TYPE=kimi",
        "--auth",
        "env",
    )

    assert result.returncode == 0, result.stderr
    accounts_conf = (workspace / "accounts.conf").read_text(encoding="utf-8")
    assert "TYPE=kimi" in accounts_conf
    assert "ENV_FILE=./accounts/api-kimi/env" in accounts_conf
    assert "KIMI_MODEL_NAME" in result.stdout
    assert "KIMI_MODEL_API_KEY" in result.stdout


def test_project_add_accepts_secondary_account(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    (workspace / "accounts.conf").write_text(
        "NAME=api-claude TYPE=claude AUTH=login\nNAME=api-codex TYPE=codex AUTH=login\n",
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
        "--secondary",
        "api-codex",
    )

    assert result.returncode == 0, result.stderr
    projects_conf = (workspace / "projects.conf").read_text(encoding="utf-8")
    assert "SECONDARY_ACCOUNTS=api-codex" in projects_conf


def test_project_add_rejects_secondary_of_same_type_as_primary(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    (workspace / "accounts.conf").write_text(
        "NAME=api-claude TYPE=claude AUTH=login\nNAME=api-claude-2 TYPE=claude AUTH=login\n",
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
        "--secondary",
        "api-claude-2",
    )

    assert result.returncode != 0
    assert "claude" in result.stderr
    assert not (workspace / "projects.conf").read_text(encoding="utf-8").strip()


def test_project_add_secondary_updates_existing_project(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    (workspace / "accounts.conf").write_text(
        "NAME=api-claude TYPE=claude AUTH=login\nNAME=api-codex TYPE=codex AUTH=login\n",
        encoding="utf-8",
    )
    (workspace / "projects.conf").write_text(
        f"NAME=repo ACCOUNT=api-claude PATH={workspace / 'repo'}\n",
        encoding="utf-8",
    )

    result = run_coderfleet(
        workspace, fake_docker_path(tmp_path), "project", "add-secondary", "repo", "api-codex",
    )

    assert result.returncode == 0, result.stderr
    projects_conf = (workspace / "projects.conf").read_text(encoding="utf-8")
    assert "SECONDARY_ACCOUNTS=api-codex" in projects_conf


def test_project_add_secondary_rejects_type_collision(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    (workspace / "accounts.conf").write_text(
        "NAME=api-claude TYPE=claude AUTH=login\nNAME=api-claude-2 TYPE=claude AUTH=login\n",
        encoding="utf-8",
    )
    (workspace / "projects.conf").write_text(
        f"NAME=repo ACCOUNT=api-claude PATH={workspace / 'repo'}\n",
        encoding="utf-8",
    )

    result = run_coderfleet(
        workspace, fake_docker_path(tmp_path), "project", "add-secondary", "repo", "api-claude-2",
    )

    assert result.returncode != 0
    assert "claude" in result.stderr
    assert "SECONDARY_ACCOUNTS" not in (workspace / "projects.conf").read_text(encoding="utf-8")


def test_project_remove_secondary_clears_field(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    (workspace / "accounts.conf").write_text(
        "NAME=api-claude TYPE=claude AUTH=login\nNAME=api-codex TYPE=codex AUTH=login\n",
        encoding="utf-8",
    )
    (workspace / "projects.conf").write_text(
        f"NAME=repo ACCOUNT=api-claude PATH={workspace / 'repo'} SECONDARY_ACCOUNTS=api-codex\n",
        encoding="utf-8",
    )

    result = run_coderfleet(
        workspace, fake_docker_path(tmp_path), "project", "remove-secondary", "repo", "api-codex",
    )

    assert result.returncode == 0, result.stderr
    assert "SECONDARY_ACCOUNTS" not in (workspace / "projects.conf").read_text(encoding="utf-8")


def test_apply_mounts_secondary_account_auth_dir_into_primary_service(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    (workspace / "accounts.conf").write_text(
        "NAME=api-claude TYPE=claude AUTH=login\nNAME=api-codex TYPE=codex AUTH=login\n",
        encoding="utf-8",
    )
    (workspace / "projects.conf").write_text(
        f"NAME=repo ACCOUNT=api-claude PATH={workspace / 'repo'} SECONDARY_ACCOUNTS=api-codex\n",
        encoding="utf-8",
    )

    result = run_coderfleet(workspace, fake_docker_path(tmp_path), "apply")

    assert result.returncode == 0, result.stderr
    compose = (workspace / "docker-compose.yml").read_text(encoding="utf-8")
    assert "claude-project-repo:" in compose
    assert "codex-project-repo:" not in compose  # 从账号不生成独立容器
    assert "./accounts/api-claude:/home/byclaw/.claude" in compose
    assert "./accounts/api-codex:/home/byclaw/.codex" in compose


def test_apply_injects_secondary_env_account_env_file(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    (workspace / "accounts.conf").write_text(
        "NAME=api-claude TYPE=claude AUTH=login\n"
        "NAME=api-codex TYPE=codex AUTH=env ENV_FILE=./accounts/api-codex/env\n",
        encoding="utf-8",
    )
    (workspace / "projects.conf").write_text(
        f"NAME=repo ACCOUNT=api-claude PATH={workspace / 'repo'} SECONDARY_ACCOUNTS=api-codex\n",
        encoding="utf-8",
    )

    result = run_coderfleet(workspace, fake_docker_path(tmp_path), "apply")

    assert result.returncode == 0, result.stderr
    compose = (workspace / "docker-compose.yml").read_text(encoding="utf-8")
    assert "./accounts/api-codex:/home/byclaw/.codex" in compose
    assert "- ./accounts/api-codex/env" in compose


def test_apply_rejects_hand_edited_type_collision(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    (workspace / "accounts.conf").write_text(
        "NAME=api-claude TYPE=claude AUTH=login\nNAME=api-claude-2 TYPE=claude AUTH=login\n",
        encoding="utf-8",
    )
    # 绕过 CLI 校验，直接手写 projects.conf 产生 TYPE 冲突
    (workspace / "projects.conf").write_text(
        f"NAME=repo ACCOUNT=api-claude PATH={workspace / 'repo'} SECONDARY_ACCOUNTS=api-claude-2\n",
        encoding="utf-8",
    )

    result = run_coderfleet(workspace, fake_docker_path(tmp_path), "apply")

    assert result.returncode != 0
    assert "claude" in result.stderr
    assert not (workspace / "docker-compose.yml").exists()


def test_build_shared_image_persists_a_build_record(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    (workspace / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")

    result = run_coderfleet(workspace, fake_docker_path(tmp_path), "build")

    assert result.returncode == 0, result.stderr
    builds_dir = workspace / "builds"
    records = list(builds_dir.glob("*.json"))
    assert len(records) == 1
    record = json.loads(records[0].read_text(encoding="utf-8"))
    assert record["kind"] == "shared"
    assert record["status"] == "succeeded"
    assert record["triggered_by"] == "cli"
    assert (builds_dir / f"{record['id']}.log").exists()


def test_build_project_image_persists_a_build_record_scoped_to_project(tmp_path: Path) -> None:
    workspace = make_two_project_workspace(tmp_path)
    (workspace / "projects" / "repo").mkdir(parents=True)
    (workspace / "projects" / "repo" / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")

    result = run_coderfleet(workspace, fake_docker_path(tmp_path), "build", "repo")

    assert result.returncode == 0, result.stderr
    records = [json.loads(p.read_text(encoding="utf-8")) for p in (workspace / "builds").glob("*.json")]
    assert len(records) == 1
    assert records[0]["kind"] == "project"
    assert records[0]["project_name"] == "repo"


def test_build_history_lists_records_newest_first_and_filters_by_project(tmp_path: Path) -> None:
    workspace = make_two_project_workspace(tmp_path)
    (workspace / "projects" / "repo").mkdir(parents=True)
    (workspace / "projects" / "repo" / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    (workspace / "projects" / "repo2").mkdir(parents=True)
    (workspace / "projects" / "repo2" / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")

    docker_path = fake_docker_path(tmp_path)
    assert run_coderfleet(workspace, docker_path, "build", "repo").returncode == 0
    assert run_coderfleet(workspace, docker_path, "build", "repo2").returncode == 0

    result = run_coderfleet(workspace, docker_path, "build-history")
    assert result.returncode == 0, result.stderr
    assert "repo" in result.stdout and "repo2" in result.stdout

    scoped = run_coderfleet(workspace, docker_path, "build-history", "--project", "repo")
    assert scoped.returncode == 0, scoped.stderr
    assert "repo2" not in scoped.stdout
    assert "repo" in scoped.stdout


def test_build_history_with_no_records_reports_empty(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)

    result = run_coderfleet(workspace, fake_docker_path(tmp_path), "build-history")

    assert result.returncode == 0, result.stderr
    assert "暂无构建记录" in result.stdout


def test_build_logs_prints_persisted_log_for_a_build_id(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    (workspace / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")

    assert run_coderfleet(workspace, fake_docker_path(tmp_path), "build").returncode == 0
    build_id = next((workspace / "builds").glob("*.json")).stem

    result = run_coderfleet(workspace, fake_docker_path(tmp_path), "build-logs", build_id)

    assert result.returncode == 0, result.stderr


def test_build_logs_missing_build_id_fails(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)

    result = run_coderfleet(workspace, fake_docker_path(tmp_path), "build-logs", "missing")

    assert result.returncode != 0
    assert "missing" in result.stderr


def test_build_all_projects_persists_a_record_per_image(tmp_path: Path) -> None:
    workspace = make_two_project_workspace(tmp_path)
    (workspace / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    (workspace / "projects" / "repo").mkdir(parents=True)
    (workspace / "projects" / "repo" / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    (workspace / "projects" / "repo2").mkdir(parents=True)
    (workspace / "projects" / "repo2" / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")

    result = run_coderfleet(workspace, fake_docker_path(tmp_path), "build", "--all-projects")

    assert result.returncode == 0, result.stderr
    records = [json.loads(p.read_text(encoding="utf-8")) for p in (workspace / "builds").glob("*.json")]
    assert len(records) == 3
    kinds = {(r["kind"], r["project_name"]) for r in records}
    assert kinds == {("shared", ""), ("project", "repo"), ("project", "repo2")}
    assert all(r["status"] == "succeeded" for r in records)
