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

from coderfleet.server.auth import AuthMiddleware
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


def test_ask_user_question_tool_accepts_bare_string_options(tmp_path: Path) -> None:
    # Real failure, found against a live deployment: Claude's first guess at the
    # tool call passed options as bare strings (["男", "女", "非二元性别 / 性别酷儿",
    # "不愿透露", "其他（可补充说明）"]) instead of {label, description} objects — a
    # completely reasonable guess for a model that's never seen this exact schema
    # before. Pydantic correctly rejected it with 5 validation errors, and Claude
    # read the error and retried correctly, so the feature technically still
    # worked — but every such guess costs a wasted round-trip and shows the human
    # an ugly raw Pydantic traceback in the chat log for no real reason, since a
    # bare string unambiguously means "this is the label." InterventionOption
    # should accept both forms transparently.
    app, sched = _make_app_and_scheduler(tmp_path)
    task = Task(
        id="ask-bare-str", status=TaskStatus.running, account="alice", type=AccountType.claude,
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
                    headers={"x-coderfleet-task-id": "ask-bare-str"},
                    httpx_client_factory=_asgi_httpx_factory(app),
                ) as (read, write, _):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        return await session.call_tool(
                            "ask_user_question",
                            {"questions": [{"question": "你的性别是什么？", "options": ["男", "女", "不愿透露"]}]},
                        )

            call_task = asyncio.create_task(call())

            for _ in range(100):
                await asyncio.sleep(0.02)
                t = sched.get_task("ask-bare-str")
                if t and t.pending_intervention:
                    break
            else:
                raise AssertionError("pending_intervention never appeared on the task")

            pending = sched.get_task("ask-bare-str").pending_intervention
            assert [o.label for o in pending.questions[0].options] == ["男", "女", "不愿透露"]

            sched.resolve_intervention(
                "ask-bare-str", pending.tool_call_id, pending.token, {"你的性别是什么？": "女"},
            )
            return await call_task
        finally:
            await session_ctx.__aexit__(None, None, None)

    result = asyncio.run(_run())
    assert not result.isError, result.content


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


def test_ask_user_question_tool_uses_claude_codes_own_tool_use_id_from_meta(tmp_path: Path) -> None:
    # Real bug, found against a live deployment: without this, PendingIntervention
    # got a freshly-minted random tool_call_id that never matched the Web UI card's
    # id (CC's own tool_use.id from the JSONL log) — every real answer submission
    # failed with "this question is no longer waiting", not just on timeout/races.
    # Claude Code sends its own tool_use id via the MCP request's `_meta` field
    # under the key "claudecode/toolUseId" (per Anthropic's own MCP integration
    # convention) — this must flow through to PendingIntervention.tool_call_id.
    #
    # The request below is built as a raw dict, NOT via mcp.types.CallToolRequestParams
    # — that class has `model_config = ConfigDict(extra="allow")` on a field that's
    # ALSO `Field(alias="_meta")`, and pydantic 2.9.2 (this project's pinned version)
    # has a real bug where that combination silently serializes the value under the
    # plain field name ("meta") instead of the alias ("_meta"), so a request built
    # through the SDK's own model never actually puts it on the wire correctly.
    # Confirmed in isolation: an `extra="allow"` model with an aliased field drops
    # the alias on `model_dump_json(by_alias=True)` in 2.9.2; the same model without
    # `extra="allow"` serializes correctly. This only affects *this Python test
    # client* — real Claude Code isn't built on this SDK/pydantic combination — so
    # sending the raw JSON-RPC body directly here tests the actual thing this test
    # needs to prove: that the *server's* deserialization + extraction is correct.
    app, sched = _make_app_and_scheduler(tmp_path)
    task = Task(
        id="ask-meta", status=TaskStatus.running, account="alice", type=AccountType.claude,
        prompt="hello", project=str(tmp_path / "repo"),
    )
    task.save(sched.tasks_dir)

    async def _run():
        session_ctx = app.state._intervention_mcp.session_manager.run()
        await session_ctx.__aenter__()
        try:
            async with streamablehttp_client(
                "http://testserver/mcp/",
                headers={"x-coderfleet-task-id": "ask-meta"},
                httpx_client_factory=_asgi_httpx_factory(app),
            ) as (read, write, get_session_id):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    session_id = get_session_id()

                async def call_raw():
                    async with _asgi_httpx_factory(app)() as client:
                        async with client.stream(
                            "POST", "/mcp/",
                            headers={
                                "accept": "application/json, text/event-stream",
                                "content-type": "application/json",
                                "mcp-session-id": session_id,
                                "mcp-protocol-version": "2025-06-18",
                                "x-coderfleet-task-id": "ask-meta",
                            },
                            json={
                                "jsonrpc": "2.0",
                                "id": 999,
                                "method": "tools/call",
                                "params": {
                                    "name": "ask_user_question",
                                    "arguments": {"questions": [{"question": "ok?"}]},
                                    "_meta": {"claudecode/toolUseId": "toolu_from_cc_real"},
                                },
                            },
                        ) as resp:
                            resp.raise_for_status()
                            async for line in resp.aiter_lines():
                                if line.startswith("data: "):
                                    return json.loads(line[len("data: "):])

                call_task = asyncio.create_task(call_raw())

                for _ in range(100):
                    await asyncio.sleep(0.02)
                    t = sched.get_task("ask-meta")
                    if t and t.pending_intervention:
                        break
                else:
                    raise AssertionError("pending_intervention never appeared on the task")

                pending = sched.get_task("ask-meta").pending_intervention
                assert pending.tool_call_id == "toolu_from_cc_real"

                sched.resolve_intervention("ask-meta", "toolu_from_cc_real", pending.token, {"ok?": "yes"})
                return await call_task
        finally:
            await session_ctx.__aexit__(None, None, None)

    result = asyncio.run(_run())
    assert "error" not in result, result
    assert json.loads(result["result"]["content"][0]["text"]) == {"ok?": "yes"}


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


def test_auth_middleware_exempts_mcp_but_still_protects_everything_else() -> None:
    # Real bug, found against a real running deployment (not caught by the tests
    # above): main.py wraps the whole app in AuthMiddleware, which requires the
    # coderfleet server's own API key on every request — but Claude's MCP client
    # has no way to know that key (and shouldn't; it's a much broader-scoped
    # secret than the one narrow capability /mcp exposes). The tests above use a
    # bare FastAPI() with no AuthMiddleware at all, so they never exercised this
    # interaction. This test mirrors main.py's real construction order
    # (middleware wraps the mount) with a live, non-empty api_key to prove /mcp
    # is exempt while confirming the middleware is still genuinely active
    # elsewhere (an unrelated path with no credential must still be rejected).
    app = FastAPI()

    @app.get("/api/whatever")
    async def _whatever():
        return {"ok": True}

    intervention_mcp = build_intervention_mcp(Scheduler(Path("/tmp/unused-for-this-test")))
    app.mount("/mcp", intervention_mcp.streamable_http_app())
    app.add_middleware(AuthMiddleware, api_key="real-secret-key")

    async def _run():
        session_ctx = intervention_mcp.session_manager.run()
        await session_ctx.__aenter__()
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://testserver",
            ) as client:
                protected = await client.get("/api/whatever")
                mcp_resp = await client.post(
                    "/mcp/",
                    headers={
                        "accept": "application/json, text/event-stream",
                        "content-type": "application/json",
                    },
                    json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                )
                return protected, mcp_resp
        finally:
            await session_ctx.__aexit__(None, None, None)

    protected_resp, mcp_resp = asyncio.run(_run())
    assert protected_resp.status_code == 401, "AuthMiddleware must still protect non-/mcp paths"
    assert mcp_resp.status_code != 401, (
        f"/mcp must be exempt from the app's own API key — Claude's MCP client can't "
        f"provide it; got {mcp_resp.status_code}: {mcp_resp.text}"
    )
