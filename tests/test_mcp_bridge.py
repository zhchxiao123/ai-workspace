"""
test_mcp_bridge.py — Intervention 的 MCP 服务器（issue #69 Slice 2）。

跟其余测试用同样的 asyncio.run() 风格，但这里额外用一个真的 mcp 客户端
（streamablehttp_client/ClientSession）连真的 Streamable HTTP transport——
用 httpx.ASGITransport 直接对接 FastAPI app，不经过真实网络/端口，但走的是
跟真实 Claude Code 完全一样的 MCP 协议握手，验证的是这一层协议接线本身，
而不是 scheduler.py 的状态机（那部分已经在 test_scheduler.py 里直接测过）。
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from coderfleet.server.mcp_bridge import build_intervention_mcp
from coderfleet.server.models import AccountType, Task, TaskStatus
from coderfleet.server.scheduler import Scheduler


@pytest.fixture(autouse=True)
def _reset_sse_starlette_exit_event():
    # sse_starlette.sse.AppStatus.should_exit_event is a process-global anyio.Event,
    # lazily created once and bound to whatever event loop was running at that
    # moment. Each test here drives its own fresh asyncio.run() (its own event
    # loop) — without resetting this between tests, the second test's SSE stream
    # tries to .wait() on an Event still bound to the first test's already-closed
    # loop and blows up with "bound to a different event loop".
    from sse_starlette.sse import AppStatus
    AppStatus.should_exit_event = None
    yield
    AppStatus.should_exit_event = None


def _asgi_httpx_factory(app):
    def factory(headers=None, timeout=None, auth=None):
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver",
            headers=headers, timeout=timeout, auth=auth,
        )
    return factory


def _make_app_and_scheduler(tmp_path: Path) -> tuple[FastAPI, Scheduler]:
    sched = Scheduler(tmp_path)
    app = FastAPI()
    intervention_mcp = build_intervention_mcp(sched)
    app.mount("/mcp", intervention_mcp.streamable_http_app())
    app.state._intervention_mcp = intervention_mcp
    return app, sched


def test_ask_user_question_tool_blocks_then_resolves_via_scheduler(tmp_path: Path) -> None:
    app, sched = _make_app_and_scheduler(tmp_path)
    task = Task(
        id="ask-real", status=TaskStatus.running, account="alice", type=AccountType.claude,
        prompt="hello", project=str(tmp_path / "repo"),
    )
    task.save(sched.tasks_dir)

    async def _run():
        session_ctx = app.state._intervention_mcp.session_manager.run()
        await session_ctx.__aenter__()
        try:
            async def call():
                async with streamablehttp_client(
                    "http://testserver/mcp/",
                    headers={"x-coderfleet-task-id": "ask-real"},
                    httpx_client_factory=_asgi_httpx_factory(app),
                ) as (read, write, _):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        return await session.call_tool(
                            "ask_user_question",
                            {"questions": [{"question": "which color?", "options": [{"label": "red"}, {"label": "blue"}]}]},
                        )

            call_task = asyncio.create_task(call())

            for _ in range(100):
                await asyncio.sleep(0.02)
                t = sched.get_task("ask-real")
                if t and t.pending_intervention:
                    break
            else:
                raise AssertionError("pending_intervention never appeared on the task")

            pending = sched.get_task("ask-real").pending_intervention
            assert pending.questions[0].question == "which color?"

            # This is exactly what POST /api/tasks/{id}/answer does.
            sched.resolve_intervention("ask-real", pending.tool_call_id, pending.token, {"which color?": "blue"})

            return await call_task
        finally:
            await session_ctx.__aexit__(None, None, None)

    result = asyncio.run(_run())
    assert not result.isError, result.content
    assert json.loads(result.content[0].text) == {"which color?": "blue"}
    assert sched.get_task("ask-real").pending_intervention is None


def test_ask_user_question_tool_rejects_missing_task_id_header(tmp_path: Path) -> None:
    app, sched = _make_app_and_scheduler(tmp_path)

    async def _run():
        session_ctx = app.state._intervention_mcp.session_manager.run()
        await session_ctx.__aenter__()
        try:
            async with streamablehttp_client(
                "http://testserver/mcp/",
                httpx_client_factory=_asgi_httpx_factory(app),
            ) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    return await session.call_tool("ask_user_question", {"questions": [{"question": "x?"}]})
        finally:
            await session_ctx.__aexit__(None, None, None)

    result = asyncio.run(_run())
    assert result.isError
    assert "X-CoderFleet-Task-Id" in result.content[0].text
