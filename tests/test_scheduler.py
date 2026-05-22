from __future__ import annotations

import sys
import asyncio
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from coderfleet.server import scheduler as scheduler_mod
from coderfleet.server.models import (
    Account,
    AccountAuth,
    AccountProxy,
    AccountType,
    Conversation,
    Project,
    Task,
    TaskStatus,
)
from coderfleet.server.scheduler import Scheduler


def _assert_detached_command(command: str, task_id: str) -> None:
    """共享断言：验证命令使用 setsid 包装、写入正确的日志和 exit 文件。"""
    assert command.startswith("mkdir -p /workspace/.coderfleet-tasks &&"), command
    assert "setsid bash -c" in command, command
    assert f"exec -a coderfleet-task-{task_id}" in command, command
    assert f"/workspace/.coderfleet-tasks/{task_id}.log" in command, command
    assert f"/workspace/.coderfleet-tasks/{task_id}.exit" in command, command
    assert command.rstrip().endswith("&"), command


def test_build_cli_command_uses_headless_codex_exec() -> None:
    command = Scheduler.build_cli_command(
        AccountType.codex,
        "fix tests",
        auto=False,
        task_id="task-1",
    )

    _assert_detached_command(command, "task-1")
    assert "codex exec --json --sandbox workspace-write" in command


def test_build_cli_command_runs_inside_container_workdir() -> None:
    command = Scheduler.build_cli_command(
        AccountType.codex,
        "fix tests",
        auto=False,
        task_id="task-1",
        container_workdir="/workspace/service-a",
    )

    _assert_detached_command(command, "task-1")
    # cd は setsid ラッパー内部にある（最上位ではない）
    assert "cd /workspace/service-a" in command
    assert not command.startswith("cd /workspace/service-a")


def test_build_cli_command_resumes_codex_thread() -> None:
    command = Scheduler.build_cli_command(
        AccountType.codex,
        "continue fixes",
        auto=False,
        task_id="task-3",
        native_session_id="thread-123",
    )

    _assert_detached_command(command, "task-3")
    assert "codex exec resume thread-123 --json" in command
    assert "thread-123" in command


def test_build_cli_command_resumes_claude_session() -> None:
    command = Scheduler.build_cli_command(
        AccountType.claude,
        "continue fixes",
        auto=False,
        task_id="task-4",
        native_session_id="session-123",
    )

    _assert_detached_command(command, "task-4")
    assert "--resume session-123" in command
    assert "--permission-mode acceptEdits" in command


def test_build_cli_command_uses_danger_sandbox_for_auto_codex() -> None:
    command = Scheduler.build_cli_command(
        AccountType.codex,
        "fix tests",
        auto=True,
        task_id="task-1",
    )

    assert "codex exec --json --sandbox danger-full-access" in command


def test_build_cli_command_uses_headless_claude_permission_modes() -> None:
    command = Scheduler.build_cli_command(
        AccountType.claude,
        "edit files",
        auto=False,
        task_id="task-2",
    )
    auto_command = Scheduler.build_cli_command(
        AccountType.claude,
        "edit files",
        auto=True,
        task_id="task-2",
    )

    assert "claude -p --permission-mode acceptEdits" in command
    assert "claude -p --dangerously-skip-permissions" in auto_command


def test_build_usage_status_command_for_codex() -> None:
    command = Scheduler.build_usage_status_command(AccountType.codex)

    assert command == "coderfleet-usage-status codex 2>&1"


def test_build_usage_status_command_skips_unsupported_cli() -> None:
    command = Scheduler.build_usage_status_command(AccountType.claude)

    assert command == ""


def test_submit_rejects_account_type_conflict(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sched = Scheduler(tmp_path)
    monkeypatch.setattr(
        sched,
        "get_accounts",
        lambda: [Account(name="alice", type=AccountType.codex)],
    )
    monkeypatch.setattr(scheduler_mod.docker_mgr, "is_container_running", lambda _name: True)

    with pytest.raises(ValueError, match="账号 .* 类型"):
        asyncio.run(
            sched.submit(
                "hello",
                account_name="alice",
                prefer_type=AccountType.claude,
            )
        )


def test_reconcile_running_tasks_marks_unrecoverable_task_failed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sched = Scheduler(tmp_path)
    task = Task(
        id="lost-1",
        status=TaskStatus.running,
        account="alice",
        type=AccountType.codex,
        prompt="hello",
        project=str(tmp_path / "repo"),
    )
    task.save(sched.tasks_dir)
    monkeypatch.setattr(sched, "is_task_process_alive", lambda _task: False)
    monkeypatch.setattr(sched, "_get_project_root", lambda _t: tmp_path)
    monkeypatch.setattr(sched, "_cleanup_container_task_files", lambda _t: None)

    recovered = asyncio.run(sched.reconcile_running_tasks())

    assert recovered == 1
    assert sched.get_task("lost-1").status == TaskStatus.failed


def test_reconcile_dead_task_with_exit_zero_marked_done(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sched = Scheduler(tmp_path)
    task = Task(
        id="done-1",
        status=TaskStatus.running,
        account="alice",
        type=AccountType.codex,
        prompt="hello",
        project=str(tmp_path / "repo"),
    )
    task.save(sched.tasks_dir)
    exit_dir = tmp_path / ".coderfleet-tasks"
    exit_dir.mkdir()
    (exit_dir / "done-1.exit").write_text("0")
    monkeypatch.setattr(sched, "is_task_process_alive", lambda _t: False)
    monkeypatch.setattr(sched, "_get_project_root", lambda _t: tmp_path)
    monkeypatch.setattr(sched, "_cleanup_container_task_files", lambda _t: None)

    asyncio.run(sched.reconcile_running_tasks())

    assert sched.get_task("done-1").status == TaskStatus.done


def test_reconcile_alive_task_schedules_reattach(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sched = Scheduler(tmp_path)
    task = Task(
        id="alive-1",
        status=TaskStatus.running,
        account="alice",
        type=AccountType.codex,
        prompt="hello",
        project=str(tmp_path / "repo"),
    )
    task.save(sched.tasks_dir)
    monkeypatch.setattr(sched, "is_task_process_alive", lambda _t: True)

    async def fake_reattach(_task: Task) -> None:
        pass

    monkeypatch.setattr(sched, "_reattach", fake_reattach)

    asyncio.run(sched.reconcile_running_tasks())

    # 状态保持 running（reattach 协程已完成 fake_reattach，但状态由它自己管理）
    assert sched.get_task("alive-1").status == TaskStatus.running


def test_extract_native_session_id_from_codex_jsonl() -> None:
    assert Scheduler.extract_native_session_id(
        AccountType.codex,
        '{"type":"thread.started","thread_id":"thread-abc"}',
    ) == "thread-abc"


def test_extract_native_session_id_from_claude_json() -> None:
    assert Scheduler.extract_native_session_id(
        AccountType.claude,
        '{"type":"result","session_id":"session-abc"}',
    ) == "session-abc"


def test_update_conversation_native_session_id(tmp_path: Path) -> None:
    sched = Scheduler(tmp_path)
    conv = Conversation(
        id="conv-1",
        name="auth flow",
        account="alice",
        type=AccountType.claude,
        project=str(tmp_path / "repo"),
    )
    conv.save(sched.conversations_dir)

    sched.update_conversation_native_session(conv.id, "session-abc", "task-1")

    loaded = sched.get_conversation("conv-1")
    assert loaded is not None
    assert loaded.native_session_id == "session-abc"
    assert loaded.last_task_id == "task-1"


def test_conversation_with_running_task_rejects_followup(tmp_path: Path) -> None:
    sched = Scheduler(tmp_path)
    conv = Conversation(
        id="conv-1",
        name="auth flow",
        account="alice",
        type=AccountType.codex,
        project=str(tmp_path / "repo"),
    )
    conv.save(sched.conversations_dir)
    Task(
        id="task-1",
        status=TaskStatus.running,
        account="alice",
        type=AccountType.codex,
        prompt="first",
        project=str(tmp_path / "repo"),
        conversation_id="conv-1",
    ).save(sched.tasks_dir)

    with pytest.raises(RuntimeError, match="任务链 .* 正在运行"):
        sched.ensure_conversation_available(conv)


def test_project_filter_matches_account_workspace_subdirectory(tmp_path: Path) -> None:
    sched = Scheduler(tmp_path)
    workspace = tmp_path / "workspace"
    project = workspace / "service-a"
    workspace.mkdir()
    project.mkdir()
    acc = Account(name="alice", type=AccountType.codex)
    (tmp_path / "projects.conf").write_text(
        f"NAME=workspace ACCOUNT=alice PATH={workspace}\n",
        encoding="utf-8",
    )

    assert sched.account_can_access_project(acc, str(project))
    assert sched.resolve_task_project(acc, str(project)) == str(project.resolve())
    project_config = sched.find_project_for_path(str(project))
    assert project_config is not None
    assert sched.container_workdir_for_project(project_config, str(project)) == "/workspace/service-a"


def test_project_filter_rejects_paths_outside_account_workspace(tmp_path: Path) -> None:
    sched = Scheduler(tmp_path)
    workspace = tmp_path / "workspace"
    other = tmp_path / "other"
    workspace.mkdir()
    other.mkdir()
    acc = Account(name="alice", type=AccountType.codex)
    (tmp_path / "projects.conf").write_text(
        f"NAME=workspace ACCOUNT=alice PATH={workspace}\n",
        encoding="utf-8",
    )

    assert not sched.account_can_access_project(acc, str(other))
    with pytest.raises(ValueError, match="未关联账号"):
        sched.resolve_task_project(acc, str(other))


def test_accounts_no_longer_require_project_path(tmp_path: Path) -> None:
    (tmp_path / "accounts.conf").write_text(
        "NAME=alice TYPE=codex\n",
        encoding="utf-8",
    )
    sched = Scheduler(tmp_path)

    accounts = sched.get_accounts()

    assert accounts == [Account(name="alice", type=AccountType.codex)]
    assert sched.get_projects() == []


def test_accounts_default_to_login_auth(tmp_path: Path) -> None:
    (tmp_path / "accounts.conf").write_text(
        "NAME=alice TYPE=claude\n",
        encoding="utf-8",
    )
    sched = Scheduler(tmp_path)

    accounts = sched.get_accounts()

    assert accounts == [
        Account(name="alice", type=AccountType.claude, auth=AccountAuth.login)
    ]


def test_accounts_parse_proxy_off(tmp_path: Path) -> None:
    (tmp_path / "accounts.conf").write_text(
        "NAME=api-claude TYPE=claude AUTH=env PROXY=off\n",
        encoding="utf-8",
    )
    sched = Scheduler(tmp_path)

    accounts = sched.get_accounts()

    assert accounts == [
        Account(
            name="api-claude",
            type=AccountType.claude,
            auth=AccountAuth.env,
            env_file="./accounts/api-claude/env",
            proxy=AccountProxy.off,
        )
    ]


def test_accounts_parse_env_auth_and_env_file(tmp_path: Path) -> None:
    (tmp_path / "accounts.conf").write_text(
        "NAME=api-claude TYPE=claude AUTH=env ENV_FILE=./accounts/api-claude/env\n",
        encoding="utf-8",
    )
    sched = Scheduler(tmp_path)

    accounts = sched.get_accounts()

    assert accounts == [
        Account(
            name="api-claude",
            type=AccountType.claude,
            auth=AccountAuth.env,
            env_file="./accounts/api-claude/env",
        )
    ]


def test_accounts_default_env_file_for_env_auth(tmp_path: Path) -> None:
    (tmp_path / "accounts.conf").write_text(
        "NAME=api-claude TYPE=claude AUTH=env\n",
        encoding="utf-8",
    )
    sched = Scheduler(tmp_path)

    accounts = sched.get_accounts()

    assert accounts == [
        Account(
            name="api-claude",
            type=AccountType.claude,
            auth=AccountAuth.env,
            env_file="./accounts/api-claude/env",
        )
    ]


def test_projects_conf_associates_project_with_account(tmp_path: Path) -> None:
    project_path = tmp_path / "repo"
    project_path.mkdir()
    (tmp_path / "projects.conf").write_text(
        f"NAME=repo ACCOUNT=alice PATH={project_path}\n",
        encoding="utf-8",
    )
    sched = Scheduler(tmp_path)

    projects = sched.get_projects()

    assert projects == [Project(name="repo", account="alice", path=str(project_path))]


def test_project_path_selects_associated_account(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project_path = tmp_path / "repo"
    service_path = project_path / "service"
    service_path.mkdir(parents=True)
    sched = Scheduler(tmp_path)
    monkeypatch.setattr(
        sched,
        "get_accounts",
        lambda: [Account(name="alice", type=AccountType.codex)],
    )
    monkeypatch.setattr(
        sched,
        "get_projects",
        lambda: [Project(name="repo", account="alice", path=str(project_path))],
    )
    monkeypatch.setattr(scheduler_mod.docker_mgr, "is_container_running", lambda _name: True)

    acc = sched.find_idle_account(prefer_project=str(service_path))

    assert acc == Account(name="alice", type=AccountType.codex)
    project = sched.find_project_for_path(str(service_path))
    assert project is not None
    assert sched.container_workdir_for_project(project, str(service_path)) == "/workspace/service"


def test_submit_uses_project_account_and_project_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_path = tmp_path / "repo"
    project_path.mkdir()
    sched = Scheduler(tmp_path)
    monkeypatch.setattr(
        sched,
        "get_accounts",
        lambda: [Account(name="alice", type=AccountType.codex)],
    )
    monkeypatch.setattr(
        sched,
        "get_projects",
        lambda: [Project(name="repo", account="alice", path=str(project_path))],
    )
    monkeypatch.setattr(scheduler_mod.docker_mgr, "is_container_running", lambda _name: True)
    monkeypatch.setattr(sched, "_run", lambda *args, **kwargs: asyncio.sleep(0))

    task = asyncio.run(sched.submit("hello", project_name="repo"))

    assert task.account == "alice"
    assert task.project == str(project_path.resolve())
