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
    BoardCardStatus,
    Conversation,
    Pipeline,
    Project,
    Schedule,
    ScheduleType,
    Task,
    TaskStatus,
    TemplateNode,
    WorkflowNodeState,
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
    assert "codex exec --json --skip-git-repo-check --sandbox workspace-write" in command


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
    assert "codex exec resume thread-123 --json --skip-git-repo-check" in command
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

    assert "codex exec --json --skip-git-repo-check --sandbox danger-full-access" in command


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


def test_build_cli_command_uses_headless_opencode_run() -> None:
    command = Scheduler.build_cli_command(
        AccountType.opencode,
        "fix tests",
        auto=False,
        task_id="task-5",
    )
    auto_command = Scheduler.build_cli_command(
        AccountType.opencode,
        "fix tests",
        auto=True,
        task_id="task-5",
        native_session_id="ses_123",
        images=["/workspace/a.png"],
    )

    _assert_detached_command(command, "task-5")
    assert "opencode run --format json" in command
    assert "fix tests" in command
    assert "--dangerously-skip-permissions" not in command
    assert "opencode run --format json --dangerously-skip-permissions --session ses_123 --file /workspace/a.png" in auto_command


def test_build_cli_command_uses_headless_kimi_prompt() -> None:
    command = Scheduler.build_cli_command(
        AccountType.kimi,
        "fix tests",
        auto=False,
        task_id="task-kimi",
    )
    resume_command = Scheduler.build_cli_command(
        AccountType.kimi,
        "continue fixes",
        auto=True,
        task_id="task-kimi-2",
        native_session_id="ses_abc123",
    )

    _assert_detached_command(command, "task-kimi")
    assert "kimi -p" in command
    assert "fix tests" in command
    assert "--output-format stream-json" in command
    assert "--session" not in command
    assert "kimi --session ses_abc123 -p" in resume_command
    assert "continue fixes" in resume_command
    assert "--output-format stream-json" in resume_command


def test_extract_native_session_id_from_kimi_resume_hint() -> None:
    text = '\n'.join([
        '{"role":"assistant","content":"done"}',
        '{"role":"meta","type":"session.resume_hint","session_id":"ses_kimi_123","command":"kimi -r ses_kimi_123"}',
    ])

    assert Scheduler.extract_native_session_id(AccountType.kimi, text) == "ses_kimi_123"


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


def test_board_card_tracks_related_task(tmp_path: Path) -> None:
    sched = Scheduler(tmp_path)
    board = sched.create_board("开发专题", "repo")
    card = sched.create_board_card(
        board.id,
        title="看板 MVP",
        project_name="repo",
        status=BoardCardStatus.todo,
    )
    task = Task(
        id="task-1",
        status=TaskStatus.done,
        account="alice",
        type=AccountType.codex,
        prompt="implement board",
        project=str(tmp_path / "repo"),
        project_name="repo",
        conversation_id="conv-1",
    )
    task.save(sched.tasks_dir)

    updated = sched.add_task_to_board_card(card.id, task.id)

    assert updated.task_ids == ["task-1"]
    assert updated.conversation_id == "conv-1"
    assert sched.list_board_cards(board_id=board.id)[0].status == BoardCardStatus.todo


def test_board_card_status_follows_task_lifecycle_conservatively(tmp_path: Path) -> None:
    sched = Scheduler(tmp_path)
    board = sched.create_board("开发专题", "repo")
    card = sched.create_board_card(board.id, title="看板 MVP", status=BoardCardStatus.planned)
    task = Task(
        id="task-1",
        status=TaskStatus.running,
        account="alice",
        type=AccountType.codex,
        prompt="implement board",
        project=str(tmp_path / "repo"),
        project_name="repo",
        board_card_id=card.id,
    )
    task.save(sched.tasks_dir)

    sched.add_task_to_board_card(card.id, task.id)

    assert sched.get_board_card(card.id).status == BoardCardStatus.running

    task.update_status(TaskStatus.done, sched.tasks_dir)
    sched._sync_board_card_for_task(task)

    assert sched.get_board_card(card.id).status == BoardCardStatus.review


def test_list_board_cards_reconciles_tasks_by_board_card_id(tmp_path: Path) -> None:
    sched = Scheduler(tmp_path)
    board = sched.create_board("开发专题", "repo")
    card = sched.create_board_card(board.id, title="工作空间管理优化")
    task = Task(
        id="task-2",
        status=TaskStatus.running,
        account="alice",
        type=AccountType.codex,
        prompt="optimize workspace",
        project=str(tmp_path / "repo"),
        project_name="repo",
        board_card_id=card.id,
    )
    task.save(sched.tasks_dir)

    cards = sched.list_board_cards(board_id=board.id)

    assert cards[0].task_ids == ["task-2"]
    assert cards[0].status == BoardCardStatus.running


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


def test_extract_native_session_id_from_opencode_json() -> None:
    assert Scheduler.extract_native_session_id(
        AccountType.opencode,
        '{"type":"step_start","sessionID":"ses_abc","part":{"type":"step-start"}}',
    ) == "ses_abc"


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


def test_run_template_resolves_default_fixed_and_runtime_targets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sched = Scheduler(tmp_path)
    repo_default = tmp_path / "default"
    repo_fixed = tmp_path / "fixed"
    repo_role = tmp_path / "role"
    for repo in [repo_default, repo_fixed, repo_role]:
        repo.mkdir()

    monkeypatch.setattr(
        sched,
        "get_accounts",
        lambda: [
            Account(name="acc-default", type=AccountType.codex),
            Account(name="acc-fixed", type=AccountType.claude),
            Account(name="acc-role", type=AccountType.codex),
        ],
    )
    monkeypatch.setattr(
        sched,
        "get_projects",
        lambda: [
            Project(name="default-proj", account="acc-default", path=str(repo_default)),
            Project(name="fixed-proj", account="acc-fixed", path=str(repo_fixed)),
            Project(name="role-proj", account="acc-role", path=str(repo_role)),
        ],
    )
    monkeypatch.setattr(scheduler_mod.docker_mgr, "is_container_running", lambda _name: True)

    tpl = sched.create_template(
        "release",
        "",
        [
            TemplateNode(node_id="plan", name="Plan", prompt_tpl="plan {{input}}", target_mode="default"),
            TemplateNode(node_id="build", name="Build", prompt_tpl="build", target_mode="fixed_project", project_name="fixed-proj", depends_on=["plan"]),
            TemplateNode(node_id="review", name="Review", prompt_tpl="review", target_mode="runtime_role", project_role="reviewer", depends_on=["build"]),
        ],
    )

    # _wait_for_task_done normally polls until a task reaches a terminal state.
    # In tests there is no real container, so we stub it to immediately mark the
    # task as done and return TaskStatus.done.
    async def _instant_done(task_id: str, **kw) -> "TaskStatus":
        t = sched.get_task(task_id)
        if t is not None:
            t.update_status(TaskStatus.done, sched.tasks_dir)
        return TaskStatus.done

    monkeypatch.setattr(sched, "_wait_for_task_done", _instant_done)
    # Stub log reading so output extraction doesn't fail on missing log files
    monkeypatch.setattr(sched, "get_log_path", lambda tid: tmp_path / "tasks" / f"{tid}.log")

    async def run_and_wait() -> "Pipeline":
        pipeline = await sched.run_template(
            tpl.id,
            "login",
            project_map={"reviewer": "role-proj"},
            default_project="default-proj",
        )
        # Allow the background pipeline coroutine to run to completion
        pending = [
            t for t in asyncio.all_tasks()
            if t.get_name().startswith(f"pipeline-{pipeline.id}")
        ]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        # Re-read from disk to pick up task_ids / node_runs written by the coroutine
        return sched.get_pipeline(pipeline.id) or pipeline

    pipeline = asyncio.run(run_and_wait())

    assert len(pipeline.task_ids) == 3
    node_ids = [run.node_id for run in pipeline.node_runs]
    assert set(node_ids) == {"plan", "build", "review"}
    resolved = {run.node_id: run.resolved_project for run in pipeline.node_runs}
    assert resolved["plan"]   == "default-proj"
    assert resolved["build"]  == "fixed-proj"
    assert resolved["review"] == "role-proj"


def test_run_template_validates_runtime_target_before_creating_pipeline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sched = Scheduler(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(sched, "get_accounts", lambda: [Account(name="acc", type=AccountType.codex)])
    monkeypatch.setattr(sched, "get_projects", lambda: [Project(name="repo", account="acc", path=str(repo))])

    tpl = sched.create_template(
        "needs role",
        "",
        [TemplateNode(node_id="review", name="Review", prompt_tpl="review", target_mode="runtime_role", project_role="reviewer")],
    )

    with pytest.raises(ValueError, match="未指定执行项目"):
        asyncio.run(sched.run_template(tpl.id, "input", project_map={}, default_project=""))

    assert sched.list_pipelines() == []


def test_run_template_allows_ephemeral_node_account_without_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sched = Scheduler(tmp_path)
    tpl = sched.create_template(
        "scratch",
        "",
        [
            TemplateNode(
                node_id="ask",
                name="Ask",
                prompt_tpl="ask {{input}}",
                execution_mode="ephemeral",
                account="alice",
            )
        ],
    )
    captured: dict = {}

    async def fake_submit(**kwargs):
        captured.update(kwargs)
        return Task(
            id="task-ask",
            status=TaskStatus.running,
            account=kwargs["account_name"],
            type=AccountType.claude,
            prompt=kwargs["prompt"],
            project="ephemeral",
        )

    async def _instant_done(_task_id: str, **_kw) -> "TaskStatus":
        return TaskStatus.done

    async def run_and_wait() -> None:
        monkeypatch.setattr(sched, "submit", fake_submit)
        monkeypatch.setattr(sched, "_wait_for_task_done", _instant_done)
        monkeypatch.setattr(sched, "get_log_path", lambda tid: tmp_path / "tasks" / f"{tid}.log")
        pipeline = await sched.run_template(tpl.id, "input", project_map={}, default_project="")
        pending = [
            t for t in asyncio.all_tasks()
            if t.get_name().startswith(f"pipeline-{pipeline.id}")
        ]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    asyncio.run(run_and_wait())

    assert captured["project_name"] == ""
    assert captured["account_name"] == "alice"
    assert captured["execution_mode"] == "ephemeral"


def test_run_template_shared_ephemeral_reuses_one_conversation_for_nodes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sched = Scheduler(tmp_path)
    monkeypatch.setattr(
        sched,
        "get_accounts",
        lambda: [Account(name="alice", type=AccountType.claude)],
    )
    tpl = sched.create_template(
        "shared scratch",
        "",
        [
            TemplateNode(
                node_id="clone",
                name="Clone",
                prompt_tpl="clone {{input}}",
                execution_mode="ephemeral",
                account="alice",
            ),
            TemplateNode(
                node_id="fix",
                name="Fix",
                prompt_tpl="fix",
                execution_mode="ephemeral",
                account="alice",
                depends_on=["clone"],
            ),
        ],
    )
    captured: list[dict] = []

    async def fake_submit(**kwargs):
        captured.append(dict(kwargs))
        return Task(
            id=f"task-{len(captured)}",
            status=TaskStatus.running,
            account=kwargs["account_name"],
            type=AccountType.claude,
            prompt=kwargs["prompt"],
            project="ephemeral",
            conversation_id=kwargs.get("conversation_id") or "",
        )

    async def _instant_done(_task_id: str, **_kw) -> "TaskStatus":
        return TaskStatus.done

    async def run_and_wait() -> "Pipeline":
        monkeypatch.setattr(sched, "submit", fake_submit)
        monkeypatch.setattr(sched, "_wait_for_task_done", _instant_done)
        monkeypatch.setattr(sched, "get_log_path", lambda tid: tmp_path / "tasks" / f"{tid}.log")
        pipeline = await sched.run_template(
            tpl.id,
            "repo",
            project_map={},
            default_project="",
            workspace_policy="shared_ephemeral",
        )
        pending = [
            t for t in asyncio.all_tasks()
            if t.get_name().startswith(f"pipeline-{pipeline.id}")
        ]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        return sched.get_pipeline(pipeline.id) or pipeline

    pipeline = asyncio.run(run_and_wait())

    assert pipeline.workspace_policy == "shared_ephemeral"
    assert pipeline.shared_conversation_id
    assert [c["conversation_id"] for c in captured] == [
        pipeline.shared_conversation_id,
        pipeline.shared_conversation_id,
    ]
    conv = sched.get_conversation(pipeline.shared_conversation_id)
    assert conv is not None
    assert conv.name == f"workflow:{pipeline.id}:shared"


def test_run_template_shared_ephemeral_uses_runtime_default_account(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sched = Scheduler(tmp_path)
    monkeypatch.setattr(
        sched,
        "get_accounts",
        lambda: [Account(name="alice", type=AccountType.claude)],
    )
    tpl = sched.create_template(
        "runtime account",
        "",
        [
            TemplateNode(
                node_id="fix",
                name="Fix",
                prompt_tpl="fix {{input}}",
                execution_mode="ephemeral",
            )
        ],
    )
    captured: dict = {}

    async def fake_submit(**kwargs):
        captured.update(kwargs)
        return Task(
            id="task-fix",
            status=TaskStatus.running,
            account=kwargs["account_name"],
            type=AccountType.claude,
            prompt=kwargs["prompt"],
            project="ephemeral",
            conversation_id=kwargs.get("conversation_id") or "",
        )

    async def _instant_done(_task_id: str, **_kw) -> "TaskStatus":
        return TaskStatus.done

    async def run_and_wait() -> "Pipeline":
        monkeypatch.setattr(sched, "submit", fake_submit)
        monkeypatch.setattr(sched, "_wait_for_task_done", _instant_done)
        monkeypatch.setattr(sched, "get_log_path", lambda tid: tmp_path / "tasks" / f"{tid}.log")
        pipeline = await sched.run_template(
            tpl.id,
            "repo",
            project_map={},
            default_project="",
            default_account="alice",
            workspace_policy="shared_ephemeral",
        )
        pending = [
            t for t in asyncio.all_tasks()
            if t.get_name().startswith(f"pipeline-{pipeline.id}")
        ]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        return sched.get_pipeline(pipeline.id) or pipeline

    pipeline = asyncio.run(run_and_wait())

    assert pipeline.default_account == "alice"
    assert pipeline.shared_conversation_id
    assert captured["account_name"] == "alice"
    assert captured["conversation_id"] == pipeline.shared_conversation_id


def test_run_template_creates_first_class_workflow_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sched = Scheduler(tmp_path)
    repo_default = tmp_path / "default"
    repo_review = tmp_path / "review"
    repo_default.mkdir()
    repo_review.mkdir()

    monkeypatch.setattr(
        sched,
        "get_accounts",
        lambda: [
            Account(name="acc-default", type=AccountType.codex),
            Account(name="acc-review", type=AccountType.codex),
        ],
    )
    monkeypatch.setattr(
        sched,
        "get_projects",
        lambda: [
            Project(name="default-proj", account="acc-default", path=str(repo_default)),
            Project(name="review-proj", account="acc-review", path=str(repo_review)),
        ],
    )
    monkeypatch.setattr(scheduler_mod.docker_mgr, "is_container_running", lambda _name: True)

    tpl = sched.create_template(
        "release",
        "",
        [
            TemplateNode(node_id="plan", name="Plan", prompt_tpl="plan {{input}}", target_mode="default"),
            TemplateNode(
                node_id="review",
                name="Review",
                prompt_tpl="review",
                target_mode="runtime_role",
                project_role="reviewer",
                depends_on=["plan"],
            ),
        ],
    )

    async def _instant_done(task_id: str, **kw) -> "TaskStatus":
        t = sched.get_task(task_id)
        if t is not None:
            t.update_status(TaskStatus.done, sched.tasks_dir)
        return TaskStatus.done

    monkeypatch.setattr(sched, "_wait_for_task_done", _instant_done)
    monkeypatch.setattr(sched, "get_log_path", lambda tid: tmp_path / "tasks" / f"{tid}.log")

    async def run_and_wait():
        pipeline = await sched.run_template(
            tpl.id,
            "login",
            project_map={"reviewer": "review-proj"},
            default_project="default-proj",
        )
        pending = [
            t for t in asyncio.all_tasks()
            if t.get_name().startswith(f"pipeline-{pipeline.id}")
        ]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        return pipeline

    pipeline = asyncio.run(run_and_wait())
    run = sched.get_workflow_run_by_legacy_pipeline_id(pipeline.id)

    assert run is not None
    assert run.id.startswith("run-")
    assert run.legacy_pipeline_id == pipeline.id
    assert run.template_id == tpl.id
    assert run.status == "succeeded"
    assert [n.node_id for n in run.node_executions] == ["plan", "review"]
    assert run.node_executions[1].depends_on == ["plan"]
    assert {n.node_id: n.resolved_project for n in run.node_executions} == {
        "plan": "default-proj",
        "review": "review-proj",
    }
    assert all(n.state == WorkflowNodeState.succeeded for n in run.node_executions)
    assert all(n.task_id for n in run.node_executions)


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


def test_accounts_parse_opencode_env_auth(tmp_path: Path) -> None:
    (tmp_path / "accounts.conf").write_text(
        "NAME=api-opencode TYPE=opencode AUTH=env\n",
        encoding="utf-8",
    )
    sched = Scheduler(tmp_path)

    accounts = sched.get_accounts()

    assert accounts == [
        Account(
            name="api-opencode",
            type=AccountType.opencode,
            auth=AccountAuth.env,
            env_file="./accounts/api-opencode/env",
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


def test_submit_schedules_ephemeral_task_without_running_immediately(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_path = tmp_path / "repo"
    project_path.mkdir()
    sched = Scheduler(tmp_path)
    monkeypatch.setattr(
        sched,
        "get_accounts",
        lambda: [Account(name="alice", type=AccountType.claude)],
    )
    monkeypatch.setattr(
        sched,
        "get_projects",
        lambda: [Project(name="repo", account="alice", path=str(project_path))],
    )

    async def fail_if_started(*_args, **_kwargs):
        raise AssertionError("scheduled ephemeral task should not start immediately")

    monkeypatch.setattr(sched, "_run_ephemeral_task", fail_if_started)

    task = asyncio.run(
        sched.submit(
            "hello",
            project_name="repo",
            execution_mode="ephemeral",
            execute_at="2999-01-01T00:00:00",
        )
    )

    assert task.status == TaskStatus.scheduled
    assert task.ephemeral is True
    assert task.execution_mode == "ephemeral"
    assert task.id not in sched._running
    assert sched.get_log_path(task.id).read_text(encoding="utf-8") == ""


def test_start_pending_ephemeral_task_uses_ephemeral_runner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sched = Scheduler(tmp_path)
    task = Task(
        id="task-pending-eph",
        status=TaskStatus.pending,
        account="alice",
        type=AccountType.claude,
        prompt="hello",
        project="ephemeral",
        project_name="",
        ephemeral=True,
        execution_mode="ephemeral",
    )
    task.save(sched.tasks_dir)
    monkeypatch.setattr(
        sched,
        "get_accounts",
        lambda: [Account(name="alice", type=AccountType.claude)],
    )

    called = asyncio.Event()

    async def fake_ephemeral_runner(*_args, **_kwargs):
        called.set()
        await asyncio.sleep(60)

    async def run_start() -> None:
        monkeypatch.setattr(sched, "_run_ephemeral_task", fake_ephemeral_runner)
        await sched._start_pending_task(task)
        await asyncio.wait_for(called.wait(), timeout=1)
        bg = sched._running.pop(task.id)
        bg.cancel()
        await asyncio.gather(bg, return_exceptions=True)

    asyncio.run(run_start())

    saved = sched.get_task(task.id)
    assert saved is not None
    assert saved.status == TaskStatus.running
    assert saved.ephemeral is True
    assert saved.execution_mode == "ephemeral"


def test_submit_ephemeral_conversation_records_retention_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sched = Scheduler(tmp_path)
    monkeypatch.setattr(
        sched,
        "get_accounts",
        lambda: [Account(name="alice", type=AccountType.claude)],
    )

    started = asyncio.Event()

    async def fake_ephemeral_runner(*_args, **_kwargs):
        started.set()
        await asyncio.sleep(60)

    async def run_submit() -> Task:
        monkeypatch.setattr(sched, "_run_ephemeral_task", fake_ephemeral_runner)
        task = await sched.submit(
            "hello",
            account_name="alice",
            execution_mode="ephemeral",
            conversation_name="scratch",
            ephemeral_retention="keep_until_ttl",
            ephemeral_ttl_minutes=30,
        )
        await asyncio.wait_for(started.wait(), timeout=1)
        bg = sched._running.pop(task.id)
        bg.cancel()
        await asyncio.gather(bg, return_exceptions=True)
        return task

    task = asyncio.run(run_submit())
    conv = sched.get_conversation(task.conversation_id)

    assert task.ephemeral_retention == "keep_until_ttl"
    assert task.ephemeral_ttl_minutes == 30
    assert conv is not None
    assert conv.ephemeral is True
    assert conv.ephemeral_retention == "keep_until_ttl"
    assert conv.ephemeral_ttl_minutes == 30
    assert conv.ephemeral_expires_at


def test_trigger_schedule_passes_execution_mode_and_output_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sched = Scheduler(tmp_path)
    schedule = Schedule(
        id="sched-1",
        name="nightly",
        prompt="hello",
        project_name="repo",
        schedule_type=ScheduleType.daily,
        execution_mode="ephemeral",
        output_dir="/tmp/out",
    )

    captured: dict = {}

    async def fake_submit(**kwargs):
        captured.update(kwargs)
        return Task(
            id="task-1",
            status=TaskStatus.running,
            account="alice",
            type=AccountType.claude,
            prompt=kwargs["prompt"],
            project="ephemeral",
        )

    monkeypatch.setattr(sched, "submit", fake_submit)

    asyncio.run(sched._trigger_schedule(schedule))

    assert captured["execution_mode"] == "ephemeral"
    assert captured["output_dir"] == "/tmp/out"
    assert schedule.last_task_id == "task-1"


def test_trigger_ephemeral_schedule_allows_account_without_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sched = Scheduler(tmp_path)
    schedule = Schedule(
        id="sched-eph",
        name="scratch",
        prompt="hello",
        project_name="",
        account="alice",
        schedule_type=ScheduleType.daily,
        execution_mode="ephemeral",
        ephemeral_retention="keep_until_ttl",
    )
    captured: dict = {}

    async def fake_submit(**kwargs):
        captured.update(kwargs)
        return Task(
            id="task-eph-sched",
            status=TaskStatus.running,
            account=kwargs["account_name"],
            type=AccountType.claude,
            prompt=kwargs["prompt"],
            project="ephemeral",
        )

    monkeypatch.setattr(sched, "submit", fake_submit)

    asyncio.run(sched._trigger_schedule(schedule))

    assert captured["project_name"] == ""
    assert captured["account_name"] == "alice"
    assert captured["execution_mode"] == "ephemeral"
    assert captured["conversation_name"] == "schedule:sched-eph"


def test_trigger_schedule_runs_workflow_template(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sched = Scheduler(tmp_path)
    schedule = Schedule(
        id="sched-wf",
        name="nightly workflow",
        prompt="fallback input",
        project_name="",
        target_type="workflow",
        template_id="tpl-1",
        workflow_input="openclaw/openclaw",
        default_account="alice",
        workspace_policy="shared_ephemeral",
        schedule_type=ScheduleType.daily,
    )
    captured: dict = {}

    async def fake_run_template(**kwargs):
        captured.update(kwargs)
        return Pipeline(
            id="pipe-1",
            name="run",
            template_id=kwargs["template_id"],
            trigger_input=kwargs["input_str"],
            default_account=kwargs["default_account"],
            workspace_policy=kwargs["workspace_policy"],
        )

    monkeypatch.setattr(sched, "run_template", fake_run_template)

    asyncio.run(sched._trigger_schedule(schedule))

    assert captured == {
        "template_id": "tpl-1",
        "input_str": "openclaw/openclaw",
        "project_map": {},
        "default_project": "",
        "default_account": "alice",
        "workspace_policy": "shared_ephemeral",
    }
    assert schedule.last_run_type == "workflow"
    assert schedule.last_workflow_run_id == "pipe-1"
    assert schedule.last_task_id == ""


def test_submit_pipeline_node_passes_node_execution_mode_and_output_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sched = Scheduler(tmp_path)
    pipeline = sched.create_pipeline("wf")
    node = TemplateNode(
        node_id="build",
        name="Build",
        prompt_tpl="build {{input}}",
        execution_mode="ephemeral",
        output_dir="/tmp/node-out",
    )
    captured: dict = {}

    async def fake_submit(**kwargs):
        captured.update(kwargs)
        return Task(
            id="task-build",
            status=TaskStatus.running,
            account="alice",
            type=AccountType.claude,
            prompt=kwargs["prompt"],
            project="ephemeral",
        )

    monkeypatch.setattr(sched, "submit", fake_submit)

    task = asyncio.run(
        sched._submit_pipeline_node(
            node,
            pipeline.id,
            {"build": "repo"},
            "input",
            {},
        )
    )

    updated = sched.get_pipeline(pipeline.id)
    assert task.id == "task-build"
    assert captured["execution_mode"] == "ephemeral"
    assert captured["output_dir"] == "/tmp/node-out"
    assert updated is not None
    assert updated.node_runs[0].execution_mode == "ephemeral"
    assert updated.node_runs[0].output_dir == "/tmp/node-out"


def test_ephemeral_task_retention_starts_session_container_then_execs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class EmptyStdout:
        async def read(self, _size: int) -> bytes:
            return b""

    class FakeStartProcess:
        pid = 111
        returncode = 0

        async def communicate(self):
            self.returncode = 0
            return (b"container-id\n", None)

    class FakeExecProcess:
        pid = 222
        returncode = 0
        stdout = EmptyStdout()

        async def wait(self) -> int:
            self.returncode = 0
            return 0

    calls: list[tuple[str, ...]] = []
    sched = Scheduler(tmp_path)
    conversation = Conversation(
        id="conv-eph",
        name="scratch",
        account="alice",
        type=AccountType.claude,
        project="ephemeral",
        ephemeral=True,
        ephemeral_retention="keep_until_ttl",
        ephemeral_ttl_minutes=45,
    )
    conversation.save(sched.conversations_dir)
    task = Task(
        id="task-eph-retain",
        status=TaskStatus.running,
        account="alice",
        type=AccountType.claude,
        prompt="hello",
        project=str(sched._get_session_dir(conversation.id)),
        conversation_id=conversation.id,
        ephemeral=True,
        execution_mode="ephemeral",
        ephemeral_retention="keep_until_ttl",
        ephemeral_ttl_minutes=45,
    )
    task.save(sched.tasks_dir)
    acc = Account(name="alice", type=AccountType.claude)
    log_path = sched.get_log_path(task.id)
    sched._write_log_header(log_path, task, acc)

    async def fake_create_subprocess_exec(*args, **_kwargs):
        calls.append(args)
        if args[:2] == ("docker", "run"):
            return FakeStartProcess()
        return FakeExecProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr(sched, "_docker_container_running", lambda _name: False)
    monkeypatch.setattr(sched, "_get_ephemeral_network", lambda _acc: None)
    monkeypatch.setattr(sched, "_get_account_image", lambda _acc, _project=None: "coderfleet:test")

    asyncio.run(sched._run_ephemeral_task(task, acc, log_path, auto=False, conversation=conversation, session_dir=sched._get_session_dir(conversation.id)))

    assert calls[0][:2] == ("docker", "run")
    assert "--rm" not in calls[0]
    assert "-d" in calls[0]
    assert calls[1][:2] == ("docker", "exec")
    saved_conv = sched.get_conversation(conversation.id)
    assert saved_conv is not None
    assert saved_conv.ephemeral_container_name == "coderfleet-eph-session-conv-eph"
    assert saved_conv.ephemeral_expires_at


def test_ephemeral_task_records_stdout_chunks_without_newlines(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeStdout:
        def __init__(self, chunks: list[bytes]):
            self.chunks = chunks

        async def read(self, _size: int) -> bytes:
            await asyncio.sleep(0)
            if not self.chunks:
                return b""
            return self.chunks.pop(0)

    class FakeProcess:
        def __init__(self) -> None:
            utf8_text = "中文过程".encode("utf-8")
            self.pid = 1234
            self.returncode = 0
            self.stdout = FakeStdout([
                b'{"type":"system","session_id":"',
                b'sess-ephemeral-1"}',
                b'partial-without-newline',
                utf8_text[:2],
                utf8_text[2:],
            ])

        async def wait(self) -> int:
            self.returncode = 0
            return 0

    sched = Scheduler(tmp_path)
    task = Task(
        id="task-eph",
        status=TaskStatus.running,
        account="alice",
        type=AccountType.claude,
        prompt="hello",
        project="ephemeral",
        ephemeral=True,
    )
    acc = Account(name="alice", type=AccountType.claude)
    log_path = sched.get_log_path(task.id)
    sched._write_log_header(log_path, task, acc)

    async def fake_create_subprocess_exec(*_args, **_kwargs):
        return FakeProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr(sched, "_get_ephemeral_network", lambda _acc: None)
    monkeypatch.setattr(sched, "_get_account_image", lambda _acc, _project=None: "coderfleet:test")

    asyncio.run(sched._run_ephemeral_task(task, acc, log_path, auto=False))

    saved = sched.get_task(task.id)
    assert saved is not None
    assert saved.status == TaskStatus.done
    assert saved.native_session_id == "sess-ephemeral-1"
    text = log_path.read_text(encoding="utf-8")
    assert '{"type":"system","session_id":"sess-ephemeral-1"}' in text
    assert "partial-without-newline" in text
    assert "中文过程" in text


def test_ephemeral_task_passes_configured_proxy_to_entrypoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class EmptyStdout:
        async def read(self, _size: int) -> bytes:
            return b""

    class FakeProcess:
        pid = 5678
        returncode = 0
        stdout = EmptyStdout()

        async def wait(self) -> int:
            return 0

    captured_args: tuple[str, ...] = ()
    (tmp_path / "config.conf").write_text(
        "RELAY_IP=10.8.0.2 RELAY_LISTEN_PORT=18080 NO_PROXY=localhost,127.0.0.1,example.test\n",
        encoding="utf-8",
    )

    sched = Scheduler(tmp_path)
    task = Task(
        id="task-eph-proxy",
        status=TaskStatus.running,
        account="alice",
        type=AccountType.claude,
        prompt="hello",
        project="ephemeral",
        ephemeral=True,
    )
    acc = Account(name="alice", type=AccountType.claude)
    log_path = sched.get_log_path(task.id)
    sched._write_log_header(log_path, task, acc)

    async def fake_create_subprocess_exec(*args, **_kwargs):
        nonlocal captured_args
        captured_args = args
        return FakeProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr(sched, "_get_ephemeral_network", lambda _acc: "coderfleet_intnet")
    monkeypatch.setattr(sched, "_get_account_image", lambda _acc, _project=None: "coderfleet:test")

    asyncio.run(sched._run_ephemeral_task(task, acc, log_path, auto=False))

    assert "HTTP_PROXY=http://10.8.0.2:18080" in captured_args
    assert "HTTPS_PROXY=http://10.8.0.2:18080" in captured_args
    assert "ALL_PROXY=http://10.8.0.2:18080" in captured_args
    assert "NO_PROXY=localhost,127.0.0.1,example.test" in captured_args
    assert "CODERFLEET_RELAY_IP=10.8.0.2" in captured_args
    assert "CODERFLEET_RELAY_PORT=18080" in captured_args
    assert "coderfleet_intnet" in captured_args
