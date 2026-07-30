from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from coderfleet.server.models import (
    AccountType,
    Conversation,
    Schedule,
    ScheduleResponse,
    ScheduleType,
    Task,
    TaskStatus,
)
from coderfleet.server.scheduler import Scheduler


def _source_task(scheduler: Scheduler, *, with_conversation: bool = True, task_id: str = "task-source") -> Task:
    conversation_id = f"conv-for-{task_id}" if with_conversation else ""
    if with_conversation:
        Conversation(
            id=conversation_id,
            name="agent session",
            account="alice",
            type=AccountType.claude,
            project="/repo",
            project_name="repo",
            native_session_id="session-current",
        ).save(scheduler.conversations_dir)
    task = Task(
        id=task_id,
        status=TaskStatus.running,
        account="alice",
        type=AccountType.claude,
        prompt="watch CI",
        project="/repo",
        project_name="repo",
        conversation_id=conversation_id,
    )
    task.save(scheduler.tasks_dir)
    return task


def test_create_agent_schedule_stamps_creator_and_derives_scope(tmp_path: Path) -> None:
    scheduler = Scheduler(tmp_path)
    _source_task(scheduler)

    sched = asyncio.run(scheduler.create_agent_schedule(
        "task-source",
        name="daily CI check",
        prompt="check CI status",
        schedule_type=ScheduleType.daily,
        time_of_day="09:00",
    ))

    assert sched.created_by == "agent"
    assert sched.created_by_conversation_id == "conv-for-task-source"
    assert sched.project_name == "repo"
    assert sched.account == "alice"
    assert sched.target_type == "task"
    assert sched.enabled is True
    assert scheduler.get_schedule(sched.id) is not None


def test_create_agent_schedule_requires_a_conversation(tmp_path: Path) -> None:
    scheduler = Scheduler(tmp_path)
    _source_task(scheduler, with_conversation=False)

    with pytest.raises(ValueError, match="不属于 Conversation"):
        asyncio.run(scheduler.create_agent_schedule(
            "task-source",
            name="daily CI check",
            prompt="check CI status",
            schedule_type=ScheduleType.daily,
            time_of_day="09:00",
        ))


def test_create_agent_schedule_is_idempotent(tmp_path: Path) -> None:
    scheduler = Scheduler(tmp_path)
    _source_task(scheduler)

    async def run():
        first = await scheduler.create_agent_schedule(
            "task-source", name="daily CI check", prompt="check CI status",
            schedule_type=ScheduleType.daily, time_of_day="09:00",
            idempotency_key="tool-call-1",
        )
        second = await scheduler.create_agent_schedule(
            "task-source", name="ignored retry name", prompt="ignored retry body",
            schedule_type=ScheduleType.hourly,
            idempotency_key="tool-call-1",
        )
        return first, second

    first, second = asyncio.run(run())
    assert first.id == second.id
    assert len(scheduler.list_schedules()) == 1


def test_schedule_response_surfaces_creator_fields_to_the_web_ui(tmp_path: Path) -> None:
    scheduler = Scheduler(tmp_path)
    _source_task(scheduler)

    sched = asyncio.run(scheduler.create_agent_schedule(
        "task-source", name="daily CI check", prompt="check CI status",
        schedule_type=ScheduleType.daily, time_of_day="09:00",
    ))

    response = ScheduleResponse.from_schedule(sched)
    assert response.created_by == "agent"
    assert response.created_by_conversation_id == "conv-for-task-source"


def test_list_schedules_for_task_returns_own_project_regardless_of_creator(tmp_path: Path) -> None:
    scheduler = Scheduler(tmp_path)
    _source_task(scheduler)

    own_agent_sched = asyncio.run(scheduler.create_agent_schedule(
        "task-source", name="agent's own", prompt="check CI",
        schedule_type=ScheduleType.daily, time_of_day="09:00",
    ))
    human_sched_same_project = scheduler.create_schedule(Schedule(
        id="sched-human-same-project", name="human's", prompt="backup",
        project_name="repo", schedule_type=ScheduleType.daily, time_of_day="03:00",
    ))
    scheduler.create_schedule(Schedule(
        id="sched-other-project", name="unrelated", prompt="unrelated",
        project_name="other-repo", schedule_type=ScheduleType.daily, time_of_day="03:00",
    ))

    scheds = scheduler.list_schedules_for_task("task-source")

    assert {s.id for s in scheds} == {own_agent_sched.id, human_sched_same_project.id}


def test_get_schedule_for_task_rejects_schedule_outside_own_project(tmp_path: Path) -> None:
    scheduler = Scheduler(tmp_path)
    _source_task(scheduler)
    other_project_sched = scheduler.create_schedule(Schedule(
        id="sched-other-project", name="unrelated", prompt="unrelated",
        project_name="other-repo", schedule_type=ScheduleType.daily, time_of_day="03:00",
    ))

    assert scheduler.get_schedule_for_task("task-source", other_project_sched.id) is None


def test_list_and_get_schedules_for_task_redact_webhook_token_for_schedules_not_owned_by_caller(tmp_path: Path) -> None:
    # A Schedule's webhook_token is an unauthenticated bearer credential accepted by
    # the public POST /api/webhooks/{webhook_token}/trigger endpoint (main.py) — handing
    # it out for a schedule the caller doesn't own would let any agent Conversation in
    # the project force-execute a human's (or another Conversation's) schedule, which is
    # exactly the mutation-adjacent capability _require_agent_owned_schedule exists to
    # prevent (found in matt-code-review's spec pass over issue #83).
    scheduler = Scheduler(tmp_path)
    _source_task(scheduler)

    own = asyncio.run(scheduler.create_agent_schedule(
        "task-source", name="agent's own", prompt="check CI",
        schedule_type=ScheduleType.daily, time_of_day="09:00",
    ))
    human_sched = scheduler.create_schedule(Schedule(
        id="sched-human-same-project", name="human's", prompt="backup",
        project_name="repo", schedule_type=ScheduleType.daily, time_of_day="03:00",
        webhook_enabled=True,
    ))
    assert human_sched.webhook_token

    listed = {s.id: s for s in scheduler.list_schedules_for_task("task-source")}
    assert listed[own.id].webhook_token == own.webhook_token
    assert listed[human_sched.id].webhook_token == ""

    got_own = scheduler.get_schedule_for_task("task-source", own.id)
    got_human = scheduler.get_schedule_for_task("task-source", human_sched.id)
    assert got_own is not None and got_own.webhook_token == own.webhook_token
    assert got_human is not None and got_human.webhook_token == ""

    # Redaction must not leak back into the persisted record.
    assert scheduler.get_schedule(human_sched.id).webhook_token == human_sched.webhook_token


def test_update_agent_schedule_succeeds_for_own_schedule(tmp_path: Path) -> None:
    scheduler = Scheduler(tmp_path)
    _source_task(scheduler)
    sched = asyncio.run(scheduler.create_agent_schedule(
        "task-source", name="daily CI check", prompt="check CI status",
        schedule_type=ScheduleType.daily, time_of_day="09:00",
    ))

    updated = scheduler.update_agent_schedule("task-source", sched.id, {"prompt": "check CI status again"})

    assert updated.prompt == "check CI status again"


def test_cancel_agent_schedule_deletes_own_schedule(tmp_path: Path) -> None:
    scheduler = Scheduler(tmp_path)
    _source_task(scheduler)
    sched = asyncio.run(scheduler.create_agent_schedule(
        "task-source", name="daily CI check", prompt="check CI status",
        schedule_type=ScheduleType.daily, time_of_day="09:00",
    ))

    scheduler.cancel_agent_schedule("task-source", sched.id)

    assert scheduler.get_schedule(sched.id) is None


def test_toggle_agent_schedule_flips_enabled_for_own_schedule(tmp_path: Path) -> None:
    scheduler = Scheduler(tmp_path)
    _source_task(scheduler)
    sched = asyncio.run(scheduler.create_agent_schedule(
        "task-source", name="daily CI check", prompt="check CI status",
        schedule_type=ScheduleType.daily, time_of_day="09:00",
    ))
    assert sched.enabled is True

    toggled = scheduler.toggle_agent_schedule("task-source", sched.id)

    assert toggled.enabled is False


def test_mutating_a_human_created_schedule_is_rejected(tmp_path: Path) -> None:
    scheduler = Scheduler(tmp_path)
    _source_task(scheduler)
    human_sched = scheduler.create_schedule(Schedule(
        id="sched-human", name="human's", prompt="backup",
        project_name="repo", schedule_type=ScheduleType.daily, time_of_day="03:00",
    ))

    with pytest.raises(ValueError, match="无权操作"):
        scheduler.update_agent_schedule("task-source", human_sched.id, {"prompt": "hijacked"})
    with pytest.raises(ValueError, match="无权操作"):
        scheduler.cancel_agent_schedule("task-source", human_sched.id)
    with pytest.raises(ValueError, match="无权操作"):
        scheduler.toggle_agent_schedule("task-source", human_sched.id)


def test_mutating_another_conversations_agent_schedule_is_rejected(tmp_path: Path) -> None:
    scheduler = Scheduler(tmp_path)
    _source_task(scheduler, task_id="task-a")
    _source_task(scheduler, task_id="task-b")
    other_conversations_sched = asyncio.run(scheduler.create_agent_schedule(
        "task-a", name="task a's check", prompt="check CI status",
        schedule_type=ScheduleType.daily, time_of_day="09:00",
    ))

    with pytest.raises(ValueError, match="无权操作"):
        scheduler.update_agent_schedule("task-b", other_conversations_sched.id, {"prompt": "hijacked"})
