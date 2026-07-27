from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path

import pytest

from coderfleet.server.models import (
    AccountType,
    ContinuationStatus,
    ContinuationTriggerRequest,
    ContinuationTriggerType,
    Conversation,
    Task,
    TaskStatus,
    utc_now,
)
from coderfleet.server.scheduler import Scheduler


def _source_task(scheduler: Scheduler, *, with_conversation: bool = True) -> Task:
    conversation_id = "conv-ci" if with_conversation else ""
    if with_conversation:
        Conversation(
            id=conversation_id,
            name="PR CI",
            account="alice",
            type=AccountType.claude,
            project="/repo",
            project_name="repo",
            native_session_id="session-current",
        ).save(scheduler.conversations_dir)
    task = Task(
        id="task-source",
        status=TaskStatus.running,
        account="alice",
        type=AccountType.claude,
        prompt="open PR",
        project="/repo",
        project_name="repo",
        conversation_id=conversation_id,
    )
    task.save(scheduler.tasks_dir)
    return task


def test_arm_timer_continuation_is_persistent_and_idempotent(tmp_path: Path) -> None:
    scheduler = Scheduler(tmp_path)
    _source_task(scheduler)
    trigger = ContinuationTriggerRequest(
        type=ContinuationTriggerType.timer, delay_seconds=60,
    )

    async def run():
        first = await scheduler.arm_continuation(
            "task-source", "check CI", [trigger], idempotency_key="tool-call-1",
        )
        second = await scheduler.arm_continuation(
            "task-source", "ignored retry body", [trigger], idempotency_key="tool-call-1",
        )
        return first, second

    first, second = asyncio.run(run())
    assert first.id == second.id
    assert first.status == ContinuationStatus.armed
    assert first.conversation_id == "conv-ci"
    assert scheduler.get_continuation(first.id) is not None


def test_arm_continuation_requires_a_conversation(tmp_path: Path) -> None:
    scheduler = Scheduler(tmp_path)
    _source_task(scheduler, with_conversation=False)

    with pytest.raises(ValueError, match="不属于 Conversation"):
        asyncio.run(scheduler.arm_continuation(
            "task-source",
            "check CI",
            [ContinuationTriggerRequest(type=ContinuationTriggerType.timer, delay_seconds=60)],
        ))


def test_due_timer_fires_once_and_creates_conversation_task(tmp_path: Path) -> None:
    scheduler = Scheduler(tmp_path)
    _source_task(scheduler)
    submitted: list[dict] = []

    async def fake_submit(**kwargs):
        submitted.append(kwargs)
        task = Task(
            id="task-followup",
            status=TaskStatus.pending,
            account="alice",
            type=AccountType.claude,
            prompt=kwargs["prompt"],
            project="/repo",
            project_name="repo",
            conversation_id=kwargs["conversation_id"],
            continuation_id=kwargs["continuation_id"],
        )
        task.save(scheduler.tasks_dir)
        return task

    scheduler.submit = fake_submit  # type: ignore[method-assign]

    async def run():
        continuation = await scheduler.arm_continuation(
            "task-source",
            "check CI",
            [ContinuationTriggerRequest(type=ContinuationTriggerType.timer, delay_seconds=60)],
        )
        continuation.triggers[0].fire_at = (
            utc_now() - timedelta(seconds=1)
        ).isoformat(timespec="seconds")
        continuation.save(scheduler.continuations_dir)
        await scheduler.process_due_continuations()
        await scheduler.process_due_continuations()
        return scheduler.get_continuation(continuation.id)

    fired = asyncio.run(run())
    assert fired is not None
    assert fired.status == ContinuationStatus.fired
    assert fired.result_task_id == "task-followup"
    assert len(submitted) == 1
    assert submitted[0]["conversation_id"] == "conv-ci"
    assert submitted[0]["parent_task_id"] == "task-source"


def test_cancelled_continuation_never_fires(tmp_path: Path) -> None:
    scheduler = Scheduler(tmp_path)
    _source_task(scheduler)

    async def run():
        continuation = await scheduler.arm_continuation(
            "task-source",
            "check CI",
            [ContinuationTriggerRequest(type=ContinuationTriggerType.timer, delay_seconds=60)],
        )
        scheduler.cancel_continuation(continuation.id)
        await scheduler.process_due_continuations()
        return scheduler.get_continuation(continuation.id)

    cancelled = asyncio.run(run())
    assert cancelled is not None
    assert cancelled.status == ContinuationStatus.cancelled
    assert cancelled.result_task_id == ""


def test_generic_webhook_token_is_returned_once_but_only_hash_is_persisted(tmp_path: Path) -> None:
    scheduler = Scheduler(tmp_path)
    _source_task(scheduler)

    async def run():
        continuation = await scheduler.arm_continuation(
            "task-source",
            "check deployment",
            [ContinuationTriggerRequest(type=ContinuationTriggerType.generic_webhook)],
        )
        first = scheduler.take_issued_webhook_tokens(continuation.id)
        second = scheduler.take_issued_webhook_tokens(continuation.id)
        persisted = scheduler.get_continuation(continuation.id)
        return continuation, first, second, persisted

    continuation, first, second, persisted = asyncio.run(run())
    assert set(first) == {continuation.triggers[0].id}
    assert second == {}
    assert persisted is not None
    assert persisted.triggers[0].webhook_token_hash
    assert first[continuation.triggers[0].id] not in (
        tmp_path / "continuations" / f"{continuation.id}.json"
    ).read_text()


def test_github_signal_matches_repository_pr_and_head_sha_and_deduplicates(tmp_path: Path) -> None:
    scheduler = Scheduler(tmp_path)
    _source_task(scheduler)
    submitted: list[dict] = []

    async def fake_submit(**kwargs):
        submitted.append(kwargs)
        task = Task(
            id="task-github-followup",
            status=TaskStatus.pending,
            account="alice",
            type=AccountType.claude,
            prompt=kwargs["prompt"],
            project="/repo",
            conversation_id=kwargs["conversation_id"],
            continuation_id=kwargs["continuation_id"],
        )
        task.save(scheduler.tasks_dir)
        return task

    scheduler.submit = fake_submit  # type: ignore[method-assign]

    async def run():
        continuation = await scheduler.arm_continuation(
            "task-source",
            "inspect checks",
            [ContinuationTriggerRequest(
                type=ContinuationTriggerType.github_pr_checks_completed,
                repository="Acme/Backend",
                pull_request=123,
                head_sha="ABC123",
            )],
        )
        wrong = await scheduler.signal_github_pr_checks(
            delivery_id="delivery-wrong",
            repository="acme/backend",
            pull_request=123,
            head_sha="different",
        )
        first = await scheduler.signal_github_pr_checks(
            delivery_id="delivery-1",
            repository="acme/backend",
            pull_request=123,
            head_sha="abc123",
        )
        duplicate = await scheduler.signal_github_pr_checks(
            delivery_id="delivery-1",
            repository="acme/backend",
            pull_request=123,
            head_sha="abc123",
        )
        return continuation, wrong, first, duplicate

    continuation, wrong, first, duplicate = asyncio.run(run())
    assert wrong == []
    assert [c.id for c in first] == [continuation.id]
    assert duplicate == []
    assert len(submitted) == 1


def test_restart_recovers_firing_continuation_without_duplicate_task(tmp_path: Path) -> None:
    scheduler = Scheduler(tmp_path)
    _source_task(scheduler)

    async def run():
        continuation = await scheduler.arm_continuation(
            "task-source",
            "check CI",
            [ContinuationTriggerRequest(type=ContinuationTriggerType.generic_webhook)],
        )
        continuation.status = ContinuationStatus.firing
        continuation.fired_by = continuation.triggers[0].id
        continuation.save(scheduler.continuations_dir)
        Task(
            id="already-created",
            status=TaskStatus.pending,
            account="alice",
            type=AccountType.claude,
            prompt="check CI",
            project="/repo",
            conversation_id="conv-ci",
            continuation_id=continuation.id,
        ).save(scheduler.tasks_dir)

        async def should_not_submit(**_kwargs):
            raise AssertionError("recovery must reuse the existing Task")

        scheduler.submit = should_not_submit  # type: ignore[method-assign]
        await scheduler.process_due_continuations()
        return scheduler.get_continuation(continuation.id)

    recovered = asyncio.run(run())
    assert recovered is not None
    assert recovered.status == ContinuationStatus.fired
    assert recovered.result_task_id == "already-created"
