"""契约测试：WorkflowRunResponse 必须暴露每个节点的权威执行状态。

前端 DAG 视图（workflows.js）依赖 `node_executions[].state` 渲染节点的真实
八态状态机（含 skipped / waiting_approval）。本测试锁定该契约，防止后端在
重构时退回到仅暴露 legacy pipeline 字段（node_runs / task_ids）。

对应 issue #10（工作流 DAG 改读 node_executions）。
"""
from __future__ import annotations

from coderfleet.server.models import (
    AccountType,
    NodeExecution,
    Task,
    TaskStatus,
    WorkflowNodeState,
    WorkflowRun,
    WorkflowRunResponse,
)


def _run_with_states() -> WorkflowRun:
    return WorkflowRun(
        id="wr-1",
        template_id="tpl-1",
        name="demo",
        status="running",
        node_executions=[
            NodeExecution(node_id="a", name="build", state=WorkflowNodeState.succeeded, task_id="t-a"),
            NodeExecution(node_id="b", name="branch", state=WorkflowNodeState.skipped, depends_on=["a"]),
            NodeExecution(node_id="c", name="gate", state=WorkflowNodeState.waiting_approval, depends_on=["a"]),
        ],
    )


def _task(task_id: str) -> Task:
    return Task(
        id=task_id,
        status=TaskStatus.done,
        account="claude-1",
        type=AccountType.claude,
        prompt="do it",
        project="/workspace",
    )


def test_from_run_preserves_authoritative_node_states() -> None:
    resp = WorkflowRunResponse.from_run(_run_with_states(), tasks=[_task("t-a")])
    states = {n.node_id: n.state for n in resp.node_executions}
    assert states["a"] == WorkflowNodeState.succeeded
    assert states["b"] == WorkflowNodeState.skipped
    assert states["c"] == WorkflowNodeState.waiting_approval


def test_from_run_keeps_legacy_compat_fields() -> None:
    # legacy node_runs / task_ids 仍需填充，以支持尚未迁移的历史运行回退渲染。
    resp = WorkflowRunResponse.from_run(_run_with_states(), tasks=[_task("t-a")])
    assert resp.task_ids == ["t-a"]
    assert {n.node_id for n in resp.node_runs} == {"a", "b", "c"}


def test_skipped_and_approval_states_are_distinct_from_pending() -> None:
    # skipped / waiting_approval 不能被塌缩成 pending，否则前端无法与"待执行"区分。
    resp = WorkflowRunResponse.from_run(_run_with_states())
    state_values = {n.state for n in resp.node_executions}
    assert WorkflowNodeState.skipped in state_values
    assert WorkflowNodeState.waiting_approval in state_values
    assert WorkflowNodeState.pending not in state_values
