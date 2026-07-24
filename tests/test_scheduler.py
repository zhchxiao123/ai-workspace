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
    NodeExecution,
    Task,
    TaskStatus,
    TemplateNode,
    WorkflowNodeState,
    WorkflowRun,
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


def test_build_cli_command_passes_claude_model() -> None:
    command = Scheduler.build_cli_command(
        AccountType.claude,
        "edit files",
        auto=False,
        task_id="task-model",
        model="sonnet-custom",
    )

    assert "claude -p --permission-mode acceptEdits" in command
    assert "--model sonnet-custom" in command


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


def test_build_cli_command_uses_grok_without_forced_session_id() -> None:
    command = Scheduler.build_cli_command(
        AccountType.grok,
        "fix tests",
        auto=False,
        task_id="task-grok",
    )
    auto_command = Scheduler.build_cli_command(
        AccountType.grok,
        "fix tests",
        auto=True,
        task_id="task-grok",
    )

    _assert_detached_command(command, "task-grok")
    assert "grok -p" in command
    assert "--output-format streaming-json" in command
    assert "--session-id" not in command
    assert " -s " not in command
    assert "grok_session_id=" not in command
    assert "--always-approve" in auto_command


def test_build_cli_command_resumes_grok_session() -> None:
    command = Scheduler.build_cli_command(
        AccountType.grok,
        "continue fixes",
        auto=False,
        task_id="task-grok-2",
        native_session_id="00000000-0000-4000-8000-000000000001",
    )

    _assert_detached_command(command, "task-grok-2")
    assert "grok_session_id=" in command
    assert "--resume 00000000-0000-4000-8000-000000000001" in command
    assert "--session-id" not in command
    assert " -s " not in command


def test_extract_native_session_id_from_grok_streaming_json() -> None:
    text = '\n'.join([
        '{"type":"message","content":"working"}',
        '{"type":"end","sessionId":"00000000-0000-4000-8000-000000000001"}',
    ])

    assert Scheduler.extract_native_session_id(
        AccountType.grok,
        text,
    ) == "00000000-0000-4000-8000-000000000001"


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
    board = sched.create_board("开发专题")
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

    # 卡片不再直接持有 task_ids；任务通过 board_card_id 反查
    assert sched.task_ids_for_board_card(updated) == ["task-1"]
    assert sched.get_task("task-1").board_card_id == card.id
    assert updated.conversation_id == "conv-1"
    assert sched.list_board_cards(board_id=board.id)[0].status == BoardCardStatus.todo


def test_board_card_status_follows_task_lifecycle_conservatively(tmp_path: Path) -> None:
    sched = Scheduler(tmp_path)
    board = sched.create_board("开发专题")
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


def _running_card_with_task(sched: Scheduler, tmp_path: Path) -> tuple:
    board = sched.create_board("开发专题")
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
    return card, task


@pytest.mark.parametrize("terminal_status", [TaskStatus.failed, TaskStatus.killed])
def test_board_card_moves_to_blocked_when_task_fails_or_is_killed(
    tmp_path: Path, terminal_status: TaskStatus
) -> None:
    sched = Scheduler(tmp_path)
    card, task = _running_card_with_task(sched, tmp_path)

    task.update_status(terminal_status, sched.tasks_dir)
    sched._sync_board_card_for_task(task)

    assert sched.get_board_card(card.id).status == BoardCardStatus.blocked


def test_blocked_board_card_recovers_to_running_on_retry(tmp_path: Path) -> None:
    sched = Scheduler(tmp_path)
    card, task = _running_card_with_task(sched, tmp_path)

    task.update_status(TaskStatus.failed, sched.tasks_dir)
    sched._sync_board_card_for_task(task)
    assert sched.get_board_card(card.id).status == BoardCardStatus.blocked

    # 重试：新任务开始运行，卡片自动从 blocked 恢复到 running
    retry = Task(
        id="task-2",
        status=TaskStatus.running,
        account="alice",
        type=AccountType.codex,
        prompt="retry board",
        project=str(tmp_path / "repo"),
        project_name="repo",
        board_card_id=card.id,
    )
    retry.save(sched.tasks_dir)
    sched._sync_board_card_for_task(retry)

    assert sched.get_board_card(card.id).status == BoardCardStatus.running


def test_update_workflow_node_appends_attempt_history(tmp_path: Path) -> None:
    sched = Scheduler(tmp_path)
    run = WorkflowRun(
        id="wr-1",
        legacy_pipeline_id="pipe-1",
        node_executions=[NodeExecution(node_id="a", name="build")],
    )
    run.save(sched.workflow_runs_dir)

    # 第一次失败尝试
    sched._update_workflow_node(
        "pipe-1", "a",
        state=WorkflowNodeState.running,
        append_attempt={"task_id": "t1", "state": "failed", "finished_at": "2026-07-10T10:00:00"},
    )
    # 第二次尝试成功
    sched._update_workflow_node(
        "pipe-1", "a",
        state=WorkflowNodeState.succeeded,
        append_attempt={"task_id": "t2", "state": "succeeded", "finished_at": "2026-07-10T10:05:00"},
    )

    reloaded = sched.get_workflow_run_by_legacy_pipeline_id("pipe-1")
    node = reloaded.node_executions[0]
    assert node.state == WorkflowNodeState.succeeded
    assert [a["task_id"] for a in node.attempts] == ["t1", "t2"]
    assert node.attempts[0]["state"] == "failed"


def test_list_board_cards_reconciles_tasks_by_board_card_id(tmp_path: Path) -> None:
    sched = Scheduler(tmp_path)
    board = sched.create_board("开发专题")
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

    assert sched.task_ids_for_board_card(cards[0]) == ["task-2"]
    assert cards[0].status == BoardCardStatus.running


def test_migrates_legacy_board_card_task_ids_to_single_ref(tmp_path: Path) -> None:
    import json
    sched = Scheduler(tmp_path)
    board = sched.create_board("开发专题")
    # 手工写入一张 legacy 卡片：既有 task_ids，又同时有 pipeline_id + conversation_id
    card_id = "bc-legacy"
    sched.board_cards_dir.mkdir(parents=True, exist_ok=True)
    (sched.board_cards_dir / f"{card_id}.json").write_text(json.dumps({
        "id": card_id, "board_id": board.id, "title": "旧卡片",
        "status": "running", "priority": "normal",
        "conversation_id": "conv-x", "pipeline_id": "pipe-x",
        "task_ids": ["task-a"],
        "created": "2026-07-01T00:00:00", "updated": "2026-07-01T00:00:00",
    }), encoding="utf-8")
    task = Task(
        id="task-a", status=TaskStatus.running, account="alice",
        type=AccountType.codex, prompt="legacy", project=str(tmp_path / "repo"),
        pipeline_id="pipe-x",
    )
    task.save(sched.tasks_dir)

    cards = sched.list_board_cards(board_id=board.id)
    migrated = next(c for c in cards if c.id == card_id)

    # legacy task 的反向指针已回填
    assert sched.get_task("task-a").board_card_id == card_id
    # 单一引用：工作流优先，会话被清除
    assert migrated.pipeline_id == "pipe-x"
    assert migrated.conversation_id == ""
    # 反查仍能拿到任务
    assert "task-a" in sched.task_ids_for_board_card(migrated)
    # 磁盘上不再持有 task_ids
    raw = json.loads((sched.board_cards_dir / f"{card_id}.json").read_text(encoding="utf-8"))
    assert "task_ids" not in raw


def test_update_board_card_enforces_single_ref_xor(tmp_path: Path) -> None:
    sched = Scheduler(tmp_path)
    board = sched.create_board("开发专题")
    card = sched.create_board_card(board.id, title="卡片")
    # 先设为工作流引用
    card = sched.update_board_card(card.id, pipeline_id="pipe-1")
    assert card.pipeline_id == "pipe-1" and card.conversation_id == ""
    # 改为会话引用会清除工作流引用
    card = sched.update_board_card(card.id, conversation_id="conv-1")
    assert card.conversation_id == "conv-1" and card.pipeline_id == ""


def test_backfills_workflow_run_for_legacy_pipeline(tmp_path: Path) -> None:
    sched = Scheduler(tmp_path)
    # legacy 手动流水线（无模板、无 node_runs）
    Pipeline(id="pipe-legacy", name="旧手动流水线").save(sched.pipelines_dir)

    runs = sched.list_workflow_runs()

    # 已持久化对应 WorkflowRun 文件，且可经 API 查看
    assert (sched.workflow_runs_dir / "pipe-legacy.json").exists()
    persisted = WorkflowRun.load(sched.workflow_runs_dir / "pipe-legacy.json")
    assert persisted.legacy_pipeline_id == "pipe-legacy"
    assert any(r.legacy_pipeline_id == "pipe-legacy" for r in runs)


def test_get_workflow_run_by_legacy_pipeline_id_scans_once_then_caches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """issue #37：真正的模板运行里，WorkflowRun.id 是新生成的，legacy_pipeline_id 指向
    旧 Pipeline.id——按旧 id 查找必然走回退分支。第一次未命中允许扫一遍
    workflow_runs/ 目录，但要把映射记进内存索引；同一个（或另一个）legacy id
    再查一次不该再触发一次全量扫描。"""
    sched = Scheduler(tmp_path)
    WorkflowRun(
        id="wr-fresh", template_id="tpl-1", name="demo", status="running",
        legacy_pipeline_id="pipe-old",
    ).save(sched.workflow_runs_dir)

    scan_calls: list[Path] = []
    orig_load_all = WorkflowRun.load_all  # bound classmethod

    def _tracking_load_all(cls, runs_dir):
        scan_calls.append(runs_dir)
        return orig_load_all(runs_dir)

    monkeypatch.setattr(WorkflowRun, "load_all", classmethod(_tracking_load_all))

    first = sched.get_workflow_run("pipe-old")
    second = sched.get_workflow_run("pipe-old")
    third = sched.get_workflow_run_by_legacy_pipeline_id("pipe-old")

    assert first is not None and first.id == "wr-fresh"
    assert second is not None and second.id == "wr-fresh"
    assert third is not None and third.id == "wr-fresh"
    assert len(scan_calls) == 1, "重复按 legacy id 查找不该反复全量扫描 workflow_runs 目录"


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


def test_submit_records_requested_model(
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
    monkeypatch.setattr(scheduler_mod.docker_mgr, "is_container_running", lambda _name: True)
    monkeypatch.setattr(sched, "_run", lambda *args, **kwargs: asyncio.sleep(0))

    task = asyncio.run(
        sched.submit("hello", project_name="repo", model="claude-sonnet-4-5-20250929")
    )

    assert task.model == "claude-sonnet-4-5-20250929"
    assert sched.get_task(task.id).model == "claude-sonnet-4-5-20250929"


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


def test_submit_rejects_past_scheduled_time_without_running_immediately(
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

    async def fail_if_started(*_args, **_kwargs):
        raise AssertionError("past scheduled task should not run immediately")

    monkeypatch.setattr(sched, "_run", fail_if_started)

    with pytest.raises(RuntimeError, match="定时时间必须晚于当前时间"):
        asyncio.run(
            sched.submit(
                "hello",
                project_name="repo",
                execute_at="2000-01-01T00:00:00",
            )
        )

    assert sched.list_tasks() == []
    assert sched._running == {}


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


def test_start_pending_task_skips_when_cancelled_before_dispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A queued task cancelled after schedule_next_tasks() snapshots it but
    before _start_pending_task actually dispatches it must not be sent to
    the CLI, and must not have its 'killed' status clobbered back to
    'running'."""
    sched = Scheduler(tmp_path)
    task = Task(
        id="task-pending-cancelled",
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

    monkeypatch.setattr(sched, "_run_ephemeral_task", fake_ephemeral_runner)

    async def run_start() -> None:
        # Simulate the race: the task is cancelled (queued -> killed) after
        # schedule_next_tasks() already snapshotted it as pending, but
        # before _start_pending_task gets to actually dispatch it.
        await sched.kill_task(task.id)
        await sched._start_pending_task(task)
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(called.wait(), timeout=0.2)

    asyncio.run(run_start())

    assert task.id not in sched._running
    saved = sched.get_task(task.id)
    assert saved is not None
    assert saved.status == TaskStatus.killed


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


def test_workflow_node_conversation_name_assigned_for_persistent_node(tmp_path: Path) -> None:
    """persistent/inherit 模式节点（默认、绝大多数工作流场景）此前从不挂 Conversation，
    导致工作流产出的对话没法在聊天界面里原生续链（issue #39）。首次执行时应该分配
    一条节点粒度的会话名，命名规则与 ephemeral 节点保持一致。"""
    sched = Scheduler(tmp_path)
    pipeline = sched.create_pipeline("wf")
    node = TemplateNode(node_id="build", name="Build", prompt_tpl="build {{input}}")  # 默认 execution_mode=inherit

    assert sched._workflow_node_conversation_id(pipeline.id, node, "release_on_finish") is None
    assert (
        sched._workflow_node_conversation_name(pipeline.id, node, "release_on_finish")
        == f"workflow:{pipeline.id}:build"
    )


def test_workflow_node_conversation_id_reuses_existing_persistent_conversation(tmp_path: Path) -> None:
    """第二次执行同一个持久节点时，应该复用已创建的 Conversation 而不是再申请一个新名字。"""
    sched = Scheduler(tmp_path)
    pipeline = sched.create_pipeline("wf")
    node = TemplateNode(node_id="build", name="Build", prompt_tpl="build {{input}}")
    conv = Conversation(
        id="conv-build", name=f"workflow:{pipeline.id}:build",
        account="alice", type=AccountType.claude, project="/srv/x", project_name="web",
    )
    conv.save(sched.conversations_dir)

    assert sched._workflow_node_conversation_id(pipeline.id, node, "release_on_finish") == "conv-build"
    assert sched._workflow_node_conversation_name(pipeline.id, node, "release_on_finish") is None


def test_workflow_node_conversation_ephemeral_release_on_finish_unchanged(tmp_path: Path) -> None:
    """回归：ephemeral 节点在 release_on_finish 保留策略下，行为保持不变——不挂会话。"""
    sched = Scheduler(tmp_path)
    pipeline = sched.create_pipeline("wf")
    node = TemplateNode(node_id="build", name="Build", prompt_tpl="build {{input}}", execution_mode="ephemeral")

    assert sched._workflow_node_conversation_id(pipeline.id, node, "release_on_finish") is None
    assert sched._workflow_node_conversation_name(pipeline.id, node, "release_on_finish") is None


def test_workflow_node_conversation_shared_ephemeral_unchanged(tmp_path: Path) -> None:
    """回归：shared_ephemeral 策略下的 ephemeral 节点仍然复用 pipeline.shared_conversation_id。"""
    sched = Scheduler(tmp_path)
    pipeline = sched.create_pipeline("wf")
    pipeline.workspace_policy = "shared_ephemeral"
    pipeline.shared_conversation_id = "conv-shared"
    pipeline.save(sched.pipelines_dir)
    node = TemplateNode(node_id="build", name="Build", prompt_tpl="build {{input}}", execution_mode="ephemeral")

    assert sched._workflow_node_conversation_id(pipeline.id, node, "keep_until_ttl") == "conv-shared"
    assert sched._workflow_node_conversation_name(pipeline.id, node, "keep_until_ttl") is None


def test_submit_pipeline_node_persistent_node_gets_conversation_name(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sched = Scheduler(tmp_path)
    pipeline = sched.create_pipeline("wf")
    node = TemplateNode(node_id="build", name="Build", prompt_tpl="build {{input}}")

    captured: dict = {}

    async def fake_submit(**kwargs):
        captured.update(kwargs)
        return Task(
            id="task-build", status=TaskStatus.running, account="alice",
            type=AccountType.claude, prompt=kwargs["prompt"], project="/srv/x",
        )

    monkeypatch.setattr(sched, "submit", fake_submit)

    asyncio.run(
        sched._submit_pipeline_node(node, pipeline.id, {"build": "repo"}, "input", {})
    )

    assert captured["conversation_id"] is None
    assert captured["conversation_name"] == f"workflow:{pipeline.id}:build"


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


# ── / skill 命令展开 ──────────────────────────────────────────

def _write_skill(tmp_path: Path, account: str, slug: str, frontmatter: str, body: str) -> None:
    skill_dir = tmp_path / "accounts" / account / "skills" / slug
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(f"---\n{frontmatter}\n---\n\n{body}\n", encoding="utf-8")


def test_expand_skill_command_substitutes_arguments(tmp_path: Path) -> None:
    sched = Scheduler(tmp_path)
    _write_skill(
        tmp_path, "alice", "review",
        "name: review\ndescription: Review a PR",
        "Review the following target: $ARGUMENTS",
    )
    out = sched._expand_skill_command("/review PR #42", "alice")
    assert out == "Review the following target: PR #42"


def test_expand_skill_command_appends_rest_without_placeholder(tmp_path: Path) -> None:
    sched = Scheduler(tmp_path)
    _write_skill(tmp_path, "alice", "standup", "name: standup\ndescription: x", "Write a standup update.")
    out = sched._expand_skill_command("/standup yesterday I shipped X", "alice")
    assert out == "Write a standup update.\n\nyesterday I shipped X"


def test_expand_skill_command_no_args_returns_content_only(tmp_path: Path) -> None:
    sched = Scheduler(tmp_path)
    _write_skill(tmp_path, "alice", "standup", "name: standup\ndescription: x", "Write a standup update.")
    out = sched._expand_skill_command("/standup", "alice")
    assert out == "Write a standup update."


def test_expand_skill_command_leaves_plain_prompt_untouched(tmp_path: Path) -> None:
    sched = Scheduler(tmp_path)
    out = sched._expand_skill_command("just a normal message, not a command", "alice")
    assert out == "just a normal message, not a command"


def test_expand_skill_command_unknown_slug_passes_through(tmp_path: Path) -> None:
    sched = Scheduler(tmp_path)
    out = sched._expand_skill_command("/does-not-exist foo", "alice")
    assert out == "/does-not-exist foo"


def test_expand_skill_command_respects_user_invocable_false(tmp_path: Path) -> None:
    sched = Scheduler(tmp_path)
    _write_skill(
        tmp_path, "alice", "internal",
        "name: internal\ndescription: x\nuser-invocable: false",
        "Should not run from chat.",
    )
    out = sched._expand_skill_command("/internal foo", "alice")
    assert out == "/internal foo"


def test_expand_skill_command_is_account_scoped(tmp_path: Path) -> None:
    sched = Scheduler(tmp_path)
    _write_skill(tmp_path, "alice", "standup", "name: standup\ndescription: x", "Alice's template.")
    out = sched._expand_skill_command("/standup today", "bob")
    assert out == "/standup today"


# ── 通知扇出 ─────────────────────────────────────────────────

def _notify_task(status=TaskStatus.done):
    return Task(
        id="t-notify", status=status, account="a1", type=AccountType.claude,
        prompt="p", project="/p", project_name="proj",
    )


def test_notify_fans_out_to_all_registered_channels(tmp_path) -> None:
    sched = Scheduler(tmp_path)
    calls: list[str] = []

    async def channel_a(task):
        calls.append(f"a:{task.id}")

    async def channel_b(task):
        calls.append(f"b:{task.id}")

    sched.register_notifier(channel_a)
    sched.register_notifier(channel_b)
    asyncio.run(sched._notify(_notify_task()))

    assert calls == ["a:t-notify", "b:t-notify"]


def test_notify_channel_failure_does_not_block_others(tmp_path) -> None:
    sched = Scheduler(tmp_path)
    calls: list[str] = []

    async def broken(task):
        raise RuntimeError("boom")

    async def healthy(task):
        calls.append(task.id)

    sched.register_notifier(broken)
    sched.register_notifier(healthy)
    asyncio.run(sched._notify(_notify_task()))

    assert calls == ["t-notify"]


def test_notify_skips_non_terminal_status(tmp_path) -> None:
    sched = Scheduler(tmp_path)
    calls: list[str] = []

    async def channel(task):
        calls.append(task.id)

    sched.register_notifier(channel)
    asyncio.run(sched._notify(_notify_task(status=TaskStatus.running)))

    assert calls == []


def test_check_auto_digest_skips_when_digest_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DIGEST_ENABLED default 是 off；关闭时绝不能提交摘要任务——它会作为一个
    真实任务运行在某个活跃项目的容器里，可能改动该项目的代码（这正是要 gate 的行为）。"""
    sched = Scheduler(tmp_path)
    monkeypatch.setattr(
        sched, "get_projects",
        lambda: [Project(name="repo", account="alice", path=str(tmp_path))],
    )

    async def fail_submit(**kwargs):
        raise AssertionError("digest task must not be submitted while DIGEST_ENABLED=off")

    monkeypatch.setattr(sched, "submit", fail_submit)

    asyncio.run(sched._check_auto_digest())

    assert not sched.digests_dir.exists() or list(sched.digests_dir.glob("*.json")) == []


# ── Intervention：非 auto 任务 CLI 主动暂停问人（issue #69） ──────────────────

def test_wait_for_intervention_answer_unblocks_on_resolve(tmp_path: Path) -> None:
    sched = Scheduler(tmp_path)
    task = Task(
        id="ask-1", status=TaskStatus.running, account="alice", type=AccountType.claude,
        prompt="hello", project=str(tmp_path / "repo"),
    )
    task.save(sched.tasks_dir)

    async def _run():
        questions = [{"question": "which color?", "options": [{"label": "red"}, {"label": "blue"}]}]
        waiter = asyncio.create_task(
            sched.wait_for_intervention_answer("ask-1", questions, timeout_seconds=5)
        )
        # Let the waiter run far enough to register the PendingIntervention before we answer it.
        await asyncio.sleep(0)
        pending = sched.get_task("ask-1").pending_intervention
        assert pending is not None, "waiter should have written pending_intervention onto the task"
        sched.resolve_intervention(
            "ask-1", pending.tool_call_id, pending.token, {"which color?": "red"}
        )
        return await waiter

    result = asyncio.run(_run())
    assert result == {"which color?": "red"}
    assert sched.get_task("ask-1").pending_intervention is None


def test_resolve_intervention_rejects_wrong_token_without_unblocking_waiter(
    tmp_path: Path,
) -> None:
    sched = Scheduler(tmp_path)
    task = Task(
        id="ask-2", status=TaskStatus.running, account="alice", type=AccountType.claude,
        prompt="hello", project=str(tmp_path / "repo"),
    )
    task.save(sched.tasks_dir)

    async def _run():
        questions = [{"question": "proceed?"}]
        waiter = asyncio.create_task(
            sched.wait_for_intervention_answer("ask-2", questions, timeout_seconds=5)
        )
        await asyncio.sleep(0)
        pending = sched.get_task("ask-2").pending_intervention
        assert pending is not None

        with pytest.raises(RuntimeError, match="token"):
            sched.resolve_intervention("ask-2", pending.tool_call_id, "wrong-token", {"proceed?": "yes"})

        # The waiter must still be pending — a bad token must not resolve it.
        assert not waiter.done()

        # Correct token still works afterwards, so the wrong attempt didn't corrupt state.
        sched.resolve_intervention("ask-2", pending.tool_call_id, pending.token, {"proceed?": "yes"})
        return await waiter

    result = asyncio.run(_run())
    assert result == {"proceed?": "yes"}


def test_resolve_intervention_rejects_missing_task_or_no_pending_question(
    tmp_path: Path,
) -> None:
    sched = Scheduler(tmp_path)
    task = Task(
        id="ask-3", status=TaskStatus.running, account="alice", type=AccountType.claude,
        prompt="hello", project=str(tmp_path / "repo"),
    )
    task.save(sched.tasks_dir)

    with pytest.raises(ValueError, match="不存在"):
        sched.resolve_intervention("does-not-exist", "tc", "tok", {})

    with pytest.raises(ValueError, match="没有待回答的问题"):
        sched.resolve_intervention("ask-3", "tc", "tok", {})


def test_wait_for_intervention_answer_times_out_and_clears_pending_state(
    tmp_path: Path,
) -> None:
    sched = Scheduler(tmp_path)
    task = Task(
        id="ask-4", status=TaskStatus.running, account="alice", type=AccountType.claude,
        prompt="hello", project=str(tmp_path / "repo"),
    )
    task.save(sched.tasks_dir)

    result = asyncio.run(
        sched.wait_for_intervention_answer("ask-4", [{"question": "still there?"}], timeout_seconds=0.05)
    )

    assert result == {"timed_out": True}
    assert sched.get_task("ask-4").pending_intervention is None


def test_resolve_intervention_rejects_answer_after_timeout(tmp_path: Path) -> None:
    sched = Scheduler(tmp_path)
    task = Task(
        id="ask-5", status=TaskStatus.running, account="alice", type=AccountType.claude,
        prompt="hello", project=str(tmp_path / "repo"),
    )
    task.save(sched.tasks_dir)

    async def _run():
        questions = [{"question": "still there?"}]
        waiter = asyncio.create_task(
            sched.wait_for_intervention_answer("ask-5", questions, timeout_seconds=0.05)
        )
        await asyncio.sleep(0)
        pending = sched.get_task("ask-5").pending_intervention
        assert pending is not None

        timed_out_result = await waiter
        assert timed_out_result == {"timed_out": True}

        with pytest.raises(RuntimeError, match="已经被回答过或已超时"):
            sched.resolve_intervention("ask-5", pending.tool_call_id, pending.token, {"still there?": "no"})

    asyncio.run(_run())


class _FakePushManager:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def send_all(self, title: str, body: str) -> None:
        self.calls.append((title, body))


def test_wait_for_intervention_answer_pushes_notification(tmp_path: Path) -> None:
    sched = Scheduler(tmp_path)
    task = Task(
        id="ask-6", status=TaskStatus.running, account="alice", type=AccountType.claude,
        prompt="hello", project=str(tmp_path / "repo"),
    )
    task.save(sched.tasks_dir)
    push = _FakePushManager()
    sched._push_manager = push

    async def _run():
        waiter = asyncio.create_task(
            sched.wait_for_intervention_answer(
                "ask-6", [{"question": "a?"}, {"question": "b?"}], timeout_seconds=5,
            )
        )
        await asyncio.sleep(0)
        pending = sched.get_task("ask-6").pending_intervention
        sched.resolve_intervention("ask-6", pending.tool_call_id, pending.token, {"a?": "x", "b?": "y"})
        await waiter

    asyncio.run(_run())

    assert len(push.calls) == 1
    title, body = push.calls[0]
    assert "ask-6" in body
    assert "2" in body  # 两个问题


def test_wait_for_intervention_answer_survives_push_manager_failure(tmp_path: Path) -> None:
    """推送渠道失败不该拖垮整个 Intervention 流程——问题该等还是等，该被回答还是能被回答。"""
    sched = Scheduler(tmp_path)
    task = Task(
        id="ask-7", status=TaskStatus.running, account="alice", type=AccountType.claude,
        prompt="hello", project=str(tmp_path / "repo"),
    )
    task.save(sched.tasks_dir)

    class _BoomPushManager:
        async def send_all(self, title, body):
            raise RuntimeError("push channel down")

    sched._push_manager = _BoomPushManager()

    async def _run():
        waiter = asyncio.create_task(
            sched.wait_for_intervention_answer("ask-7", [{"question": "a?"}], timeout_seconds=5)
        )
        await asyncio.sleep(0)
        pending = sched.get_task("ask-7").pending_intervention
        sched.resolve_intervention("ask-7", pending.tool_call_id, pending.token, {"a?": "x"})
        return await waiter

    result = asyncio.run(_run())
    assert result == {"a?": "x"}
