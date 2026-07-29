"""test_account_type_registry.py — pi 账号类型：命令构建 + session-id 提取（纯函数，无 I/O）。"""
from __future__ import annotations

import json

from coderfleet.account_type_registry import (
    ACCOUNT_TYPES,
    CostDelta,
    CostFirstSeen,
    CostTotal,
    FinalText,
    TextChunk,
    ToolIntent,
    ToolOutcome,
    UsageDelta,
    UsageProgress,
    UsageTotal,
    _build_pi,
    _events_claude,
    _events_codex,
    _events_grok,
    _events_kimi,
    _events_opencode,
    _events_pi,
    _extract_pi,
    _shape_by_type,
)


# ── AccountTypeSpec("pi") 字段 ──────────────────────────────────────────
def test_pi_registered_with_expected_spec_fields() -> None:
    spec = ACCOUNT_TYPES["pi"]
    assert spec.id == "pi"
    assert spec.auth_dir == "/home/byclaw/.pi/agent"
    assert spec.login_cli == "pi"
    assert spec.login_args == []
    assert spec.supports_env_auth is True
    assert spec.build_inner_cmd is _build_pi
    assert spec.extract_session_id is _extract_pi


# ── _build_pi ────────────────────────────────────────────────────────────
def test_build_pi_new_session_has_no_resume_flag() -> None:
    cmd = _build_pi("hello", False, "t1", "marker1", "t1", "", [], "")
    assert "pi --mode json" in cmd
    assert "--session" not in cmd
    assert "hello" in cmd


def test_build_pi_resumes_with_session_flag() -> None:
    cmd = _build_pi("continue", False, "t1", "marker1", "t1", "sess-abc", [], "")
    assert "--session sess-abc" in cmd


def test_build_pi_passes_model_flag_when_given() -> None:
    cmd = _build_pi("hi", False, "t1", "marker1", "t1", "", [], "anthropic/claude-opus")
    assert "--model" in cmd
    assert "anthropic/claude-opus" in cmd


def test_build_pi_omits_model_flag_when_not_given() -> None:
    cmd = _build_pi("hi", False, "t1", "marker1", "t1", "", [], "")
    assert "--model" not in cmd


def test_build_pi_auto_maps_to_approve_flag() -> None:
    cmd = _build_pi("hi", True, "t1", "marker1", "t1", "", [], "")
    assert "--approve" in cmd
    assert "--no-approve" not in cmd


def test_build_pi_manual_maps_to_no_approve_flag() -> None:
    cmd = _build_pi("hi", False, "t1", "marker1", "t1", "", [], "")
    assert "--no-approve" in cmd


def test_build_pi_attaches_images_via_at_file_syntax() -> None:
    # pi's real attachment mechanism (docs/usage.md "File Arguments"):
    # `pi @screenshot.png "prompt"` — not a text hint appended to the prompt.
    cmd = _build_pi("hi", False, "t1", "marker1", "t1", "", ["/tmp/a.png"], "")
    assert "@/tmp/a.png" in cmd


def test_build_pi_attaches_multiple_images_before_the_prompt() -> None:
    cmd = _build_pi("hi", False, "t1", "marker1", "t1", "", ["/tmp/a.png", "/tmp/b.png"], "")
    assert "@/tmp/a.png" in cmd
    assert "@/tmp/b.png" in cmd
    assert cmd.index("@/tmp/a.png") < cmd.index("@/tmp/b.png") < cmd.rindex("hi")


def test_build_pi_includes_task_marker_and_env() -> None:
    cmd = _build_pi("hi", False, "task-123", "cf-marker", "task-123", "", [], "")
    assert "CODERFLEET_TASK_ID=task-123" in cmd
    assert "exec -a cf-marker" in cmd


# ── _extract_pi ──────────────────────────────────────────────────────────
def test_extract_pi_reads_session_header_id() -> None:
    log = "\n".join([
        json.dumps({"type": "session", "version": 3, "id": "abc-123", "timestamp": "t", "cwd": "/workspace"}),
        json.dumps({"type": "message_start"}),
    ])
    assert _extract_pi(log) == "abc-123"


def test_extract_pi_ignores_non_session_lines() -> None:
    log = "\n".join([
        "not json",
        json.dumps({"type": "turn_start"}),
        json.dumps({"type": "session", "id": "xyz-789"}),
    ])
    assert _extract_pi(log) == "xyz-789"


def test_extract_pi_returns_empty_when_no_session_line() -> None:
    log = json.dumps({"type": "turn_start"})
    assert _extract_pi(log) == ""


# ── _events_kimi (issue #70: normalized output-event classifiers) ────────
def test_events_kimi_string_content_yields_text_chunk() -> None:
    assert _events_kimi({"role": "assistant", "content": "Done."}) == [TextChunk("Done.")]


def test_events_kimi_list_content_yields_text_chunk_per_text_block() -> None:
    line = {
        "role": "assistant",
        "content": [
            {"type": "text", "text": "part one"},
            {"type": "image", "url": "..."},
            {"type": "text", "text": "part two"},
        ],
    }
    assert _events_kimi(line) == [TextChunk("part one"), TextChunk("part two")]


def test_events_kimi_ignores_roles_that_carry_no_output() -> None:
    # Was written against the `tool` role back when nothing classified it; #79
    # gave that role tool outcomes, so the claim moved to the roles that really
    # do stay silent.
    assert _events_kimi({"role": "meta", "type": "session.resume_hint", "session_id": "s1"}) == []
    assert _events_kimi({"role": "user", "content": "hello"}) == []


def test_events_kimi_assistant_yields_one_tool_intent_per_tool_call() -> None:
    # #71's User Story 5 claimed kimi emits no tool events. It does — OpenAI
    # style, on an array carried by an assistant-role line. See the correction
    # comment on #71.
    line = {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {"id": "tc_1", "function": {"name": "Bash", "arguments": {"command": "ls"}}},
            {"id": "tc_2", "function": {"name": "Read", "arguments": {"path": "/a"}}},
        ],
    }
    assert _events_kimi(line) == [
        ToolIntent("tc_1", "Bash", {"command": "ls"}),
        ToolIntent("tc_2", "Read", {"path": "/a"}),
    ]


def test_events_kimi_assistant_yields_text_alongside_tool_calls() -> None:
    line = {
        "role": "assistant",
        "content": "Let me look.",
        "tool_calls": [{"id": "tc_1", "function": {"name": "Bash", "arguments": {}}}],
    }
    assert _events_kimi(line) == [
        TextChunk("Let me look."),
        ToolIntent("tc_1", "Bash", {}),
    ]


def test_events_kimi_decodes_json_encoded_tool_arguments() -> None:
    line = {"role": "assistant", "tool_calls": [
        {"id": "tc_1", "function": {"name": "Bash", "arguments": '{"command": "ls"}'}},
    ]}
    assert _events_kimi(line) == [ToolIntent("tc_1", "Bash", {"command": "ls"})]


def test_events_kimi_keeps_unparseable_tool_arguments_string() -> None:
    # Neither discarded nor allowed to raise — the classifier stays total.
    line = {"role": "assistant", "tool_calls": [
        {"id": "tc_1", "function": {"name": "Bash", "arguments": "{not json"}},
    ]}
    assert _events_kimi(line) == [ToolIntent("tc_1", "Bash", {"arguments": "{not json"})]


def test_events_kimi_tool_role_yields_tool_outcome() -> None:
    line = {"role": "tool", "tool_call_id": "tc_1", "content": "README.md"}
    assert _events_kimi(line) == [ToolOutcome("tc_1", "README.md", False)]


def test_events_kimi_tool_role_reports_success_because_it_carries_no_error_flag() -> None:
    # kimi's tool lines have no error field at all, unlike every other format.
    # The decision (documented on the classifier) is to report False rather than
    # guess from the content.
    line = {"role": "tool", "tool_call_id": "tc_1", "content": "Error: no such file"}
    assert _events_kimi(line) == [ToolOutcome("tc_1", "Error: no such file", False)]


def test_events_kimi_still_reports_no_token_or_cost_usage() -> None:
    # A known gap preserved bug-for-bug since #70, explicitly not fixed here.
    line = {"role": "assistant", "content": "hi", "usage": {"input_tokens": 10, "output_tokens": 2}}
    assert _events_kimi(line) == [TextChunk("hi")]


def test_kimi_discriminates_line_shape_on_role_not_type() -> None:
    # kimi is the one type whose lines are named by `role`; `type` only
    # sub-classifies its meta lines. Read through the registry's own
    # mechanism — the coverage detector never mentions either field name.
    shape_of = ACCOUNT_TYPES["kimi"].shape_of
    assert shape_of({"role": "assistant", "content": "hi"}) == "assistant"
    assert shape_of({"role": "tool", "tool_call_id": "tc_1"}) == "tool"
    assert shape_of({"role": "meta", "type": "session.resume_hint"}) == "meta/session.resume_hint"


# ── _events_grok ───────────────────────────────────────────────────────
def test_events_grok_text_delta_yields_text_chunk() -> None:
    assert _events_grok({"type": "text_delta", "text": "ta"}) == [TextChunk("ta")]


def test_events_grok_text_prefers_data_field_over_text_field() -> None:
    assert _events_grok({"type": "text", "data": "uri", "text": "ignored"}) == [TextChunk("uri")]


def test_events_grok_text_falls_back_to_text_field_when_no_data() -> None:
    assert _events_grok({"type": "text", "text": "fallback"}) == [TextChunk("fallback")]


def test_events_grok_end_yields_usage_total() -> None:
    line = {"type": "end", "stopReason": "EndTurn", "usage": {"input_tokens": 100, "output_tokens": 20}}
    assert _events_grok(line) == [UsageTotal(100, 20)]


def test_events_grok_end_defaults_missing_usage_fields_to_zero() -> None:
    # Unlike codex's turn.completed (per-field None means "don't overwrite"),
    # grok's end event always overwrites both counts, defaulting to 0.
    assert _events_grok({"type": "end", "usage": {}}) == [UsageTotal(0, 0)]


def test_events_grok_ignores_thought_events() -> None:
    assert _events_grok({"type": "thought", "data": "thinking..."}) == []


def test_events_grok_has_no_tool_events_by_design() -> None:
    # grok's stream really does carry only thought/text/end — it has no tool
    # surface at all (renderer.js's grok line handler has no tool branch
    # either). Asserted rather than assumed, because "this format has no tool
    # events" is otherwise indistinguishable from "we forgot to classify them"
    # — which is exactly the mistake #71 made about kimi. Fed a tool-shaped
    # line from another format, grok stays silent.
    assert _events_grok({
        "type": "tool_execution_start",
        "toolCallId": "c1", "toolName": "Bash", "args": {},
    }) == []
    assert _events_grok({
        "role": "assistant",
        "tool_calls": [{"id": "c1", "function": {"name": "Bash", "arguments": "{}"}}],
    }) == []


# ── _events_pi ───────────────────────────────────────────────────────────
def test_events_pi_message_end_ignores_thinking_blocks() -> None:
    line = {
        "type": "message_end",
        "message": {
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": "reasoning..."},
                {"type": "text", "text": "Hello there"},
            ],
        },
    }
    assert _events_pi(line) == [TextChunk("Hello there")]


def test_events_pi_message_end_with_usage_yields_progress_and_cost_total() -> None:
    line = {
        "type": "message_end",
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": "Done."}],
            "usage": {"input": 100, "output": 20, "cost": {"total": 0.003}},
        },
    }
    assert _events_pi(line) == [
        TextChunk("Done."),
        UsageProgress(100, 20),
        CostTotal(0.003),
    ]


def test_events_pi_message_end_with_usage_but_no_cost_total() -> None:
    line = {
        "type": "message_end",
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": "Done."}],
            "usage": {"input": 5, "output": 1, "cost": {}},
        },
    }
    assert _events_pi(line) == [TextChunk("Done."), UsageProgress(5, 1)]


def test_events_pi_ignores_non_assistant_message_end() -> None:
    line = {"type": "message_end", "message": {"role": "user", "content": [{"type": "text", "text": "hi"}]}}
    assert _events_pi(line) == []


def test_events_pi_ignores_non_message_end_events() -> None:
    assert _events_pi({"type": "agent_end", "messages": []}) == []


def test_events_pi_tool_execution_start_yields_tool_intent() -> None:
    line = {
        "type": "tool_execution_start",
        "toolCallId": "call_abc123",
        "toolName": "Bash",
        "args": {"command": "ls -la"},
    }
    assert _events_pi(line) == [
        ToolIntent("call_abc123", "Bash", {"command": "ls -la"})
    ]


def test_events_pi_tool_execution_end_joins_result_content_blocks() -> None:
    line = {
        "type": "tool_execution_end",
        "toolCallId": "call_abc123",
        "result": {"content": [{"text": "total 0"}, {"text": "drwxr-xr-x"}]},
        "isError": False,
    }
    assert _events_pi(line) == [
        ToolOutcome("call_abc123", "total 0\ndrwxr-xr-x", False)
    ]


def test_events_pi_tool_execution_end_accepts_bare_string_result() -> None:
    line = {"type": "tool_execution_end", "toolCallId": "c1", "result": "done"}
    assert _events_pi(line) == [ToolOutcome("c1", "done", False)]


def test_events_pi_tool_execution_end_carries_error_flag() -> None:
    line = {
        "type": "tool_execution_end",
        "toolCallId": "c1",
        "result": {"content": [{"text": "boom"}]},
        "isError": True,
    }
    assert _events_pi(line) == [ToolOutcome("c1", "boom", True)]


def test_events_pi_tool_execution_end_with_unusable_result_yields_empty_text() -> None:
    line = {"type": "tool_execution_end", "toolCallId": "c1", "result": 42}
    assert _events_pi(line) == [ToolOutcome("c1", "", False)]


# ── _events_claude ─────────────────────────────────────────────────────
def test_events_claude_result_yields_final_text_and_cost_total() -> None:
    line = {"type": "result", "result": "Hello there", "cost_usd": 0.02, "session_id": "s1"}
    assert _events_claude(line) == [FinalText("Hello there"), CostTotal(0.02)]


def test_events_claude_result_falls_back_to_total_cost_usd_key() -> None:
    line = {"type": "result", "result": "Hi", "total_cost_usd": 0.05}
    assert _events_claude(line) == [FinalText("Hi"), CostTotal(0.05)]


def test_events_claude_result_with_empty_result_still_reports_cost() -> None:
    # r="" is falsy -> no FinalText, but cost is checked independently.
    line = {"type": "result", "result": "", "cost_usd": 0.01}
    assert _events_claude(line) == [CostTotal(0.01)]


def test_events_claude_assistant_yields_text_chunks_and_usage_progress() -> None:
    line = {
        "type": "assistant",
        "message": {
            "content": [{"type": "text", "text": "Hello"}, {"type": "text", "text": " there"}],
            "usage": {"input_tokens": 50, "output_tokens": 10},
        },
    }
    assert _events_claude(line) == [
        TextChunk("Hello"), TextChunk(" there"), UsageProgress(50, 10),
    ]


def test_events_claude_assistant_with_no_usage_yields_only_text() -> None:
    line = {"type": "assistant", "message": {"content": [{"type": "text", "text": "hi"}], "usage": {}}}
    assert _events_claude(line) == [TextChunk("hi")]


def test_events_claude_usage_event_yields_delta_and_cost_first_seen() -> None:
    line = {"type": "usage", "input_tokens": 5, "output_tokens": 2, "cost_usd": 0.01}
    assert _events_claude(line) == [UsageDelta(5, 2), CostFirstSeen(0.01)]


def test_events_claude_usage_event_without_cost_yields_only_delta() -> None:
    line = {"type": "usage", "input_tokens": 5, "output_tokens": 2}
    assert _events_claude(line) == [UsageDelta(5, 2)]


def test_events_claude_ignores_unknown_event_types() -> None:
    assert _events_claude({"type": "rate_limit_event"}) == []


def test_events_claude_assistant_yields_one_tool_intent_per_tool_use_block() -> None:
    # claude is the first format whose tool events are nested *inside* a line
    # rather than being the line's own shape, and the first where one line
    # yields several events.
    line = {
        "type": "assistant",
        "message": {
            "content": [
                {"type": "text", "text": "Let me look."},
                {"type": "thinking", "thinking": "reasoning..."},
                {"type": "tool_use", "id": "toolu_1", "name": "Read", "input": {"file_path": "/a"}},
                {"type": "tool_use", "id": "toolu_2", "name": "Bash", "input": {"command": "ls"}},
            ],
        },
    }
    assert _events_claude(line) == [
        TextChunk("Let me look."),
        ToolIntent("toolu_1", "Read", {"file_path": "/a"}),
        ToolIntent("toolu_2", "Bash", {"command": "ls"}),
    ]


def test_events_claude_assistant_keeps_text_and_usage_alongside_tool_intents() -> None:
    line = {
        "type": "assistant",
        "message": {
            "content": [
                {"type": "tool_use", "id": "toolu_1", "name": "Read", "input": {}},
                {"type": "text", "text": "done"},
            ],
            "usage": {"input_tokens": 7, "output_tokens": 3},
        },
    }
    assert _events_claude(line) == [
        ToolIntent("toolu_1", "Read", {}),
        TextChunk("done"),
        UsageProgress(7, 3),
    ]


def test_events_claude_user_tool_result_with_string_content() -> None:
    line = {
        "type": "user",
        "message": {"content": [
            {"type": "tool_result", "tool_use_id": "toolu_1", "content": "README.md"},
        ]},
    }
    assert _events_claude(line) == [ToolOutcome("toolu_1", "README.md", False)]


def test_events_claude_user_tool_result_with_content_block_array() -> None:
    line = {
        "type": "user",
        "message": {"content": [
            {"type": "tool_result", "tool_use_id": "toolu_1", "content": [
                {"type": "text", "text": "line one"},
                {"type": "text", "text": "line two"},
            ]},
        ]},
    }
    assert _events_claude(line) == [ToolOutcome("toolu_1", "line one\nline two", False)]


def test_events_claude_user_tool_result_keeps_text_when_array_mixes_an_image_block() -> None:
    # Read hitting an image file puts a text-less image block in the array;
    # joining naively yields "" and silently loses the result. renderer.js
    # already had to fix exactly this once.
    line = {
        "type": "user",
        "message": {"content": [
            {"type": "tool_result", "tool_use_id": "toolu_1", "content": [
                {"type": "image", "source": {"data": "base64..."}},
                {"type": "text", "text": "the actual output"},
            ]},
        ]},
    }
    assert _events_claude(line) == [ToolOutcome("toolu_1", "the actual output", False)]


def test_events_claude_user_tool_result_carries_error_flag() -> None:
    line = {
        "type": "user",
        "message": {"content": [
            {"type": "tool_result", "tool_use_id": "toolu_1", "content": "boom", "is_error": True},
        ]},
    }
    assert _events_claude(line) == [ToolOutcome("toolu_1", "boom", True)]


def test_events_claude_user_yields_one_outcome_per_tool_result_block() -> None:
    line = {
        "type": "user",
        "message": {"content": [
            {"type": "tool_result", "tool_use_id": "toolu_1", "content": "a"},
            {"type": "text", "text": "an ordinary user turn, not a tool result"},
            {"type": "tool_result", "tool_use_id": "toolu_2", "content": "b"},
        ]},
    }
    assert _events_claude(line) == [
        ToolOutcome("toolu_1", "a", False),
        ToolOutcome("toolu_2", "b", False),
    ]


def test_events_claude_user_without_tool_results_yields_nothing() -> None:
    line = {"type": "user", "message": {"content": [{"type": "text", "text": "hello"}]}}
    assert _events_claude(line) == []


# ── _events_codex ──────────────────────────────────────────────────────
def test_events_codex_legacy_message_role_yields_text_chunks() -> None:
    line = {
        "type": "message", "role": "assistant",
        "content": [{"type": "text", "text": "legacy answer"}],
    }
    assert _events_codex(line) == [TextChunk("legacy answer")]


def test_events_codex_item_completed_agent_message_yields_text_chunk() -> None:
    line = {
        "type": "item.completed",
        "item": {"id": "item_1", "type": "agent_message", "text": "Here is the final answer."},
    }
    assert _events_codex(line) == [TextChunk("Here is the final answer.")]


def test_events_codex_item_completed_agent_message_with_no_text_yields_nothing() -> None:
    line = {"type": "item.completed", "item": {"id": "item_1", "type": "agent_message", "text": ""}}
    assert _events_codex(line) == []


def test_events_codex_item_completed_yields_no_text_for_non_agent_message_items() -> None:
    # Was written against `command_execution` when no item type classified at
    # all; #77 gave that type tool events, so the "not a text item" claim moved
    # to `file_change` — a display-only surface that classifies as nothing.
    line = {"type": "item.completed", "item": {"id": "item_1", "type": "file_change", "changes": []}}
    assert _events_codex(line) == []


def test_events_codex_usage_event_is_additive_delta() -> None:
    assert _events_codex({"type": "usage", "prompt_tokens": 10, "completion_tokens": 3}) == [UsageDelta(10, 3)]
    assert _events_codex({"type": "response.usage", "input_tokens": 7, "output_tokens": 2}) == [UsageDelta(7, 2)]


def test_events_codex_turn_completed_yields_usage_total_with_both_fields() -> None:
    line = {"type": "turn.completed", "usage": {"input_tokens": 150, "output_tokens": 35}}
    assert _events_codex(line) == [UsageTotal(150, 35)]


def test_events_codex_turn_completed_leaves_missing_field_as_none() -> None:
    # A per-field None means "don't overwrite" in the generic reducer --
    # distinct from grok's end event, which always defaults missing fields
    # to 0 and overwrites both sides regardless.
    line = {"type": "turn.completed", "usage": {"input_tokens": 150}}
    assert _events_codex(line) == [UsageTotal(150, None)]


def test_events_codex_turn_completed_with_non_dict_usage_yields_nothing() -> None:
    assert _events_codex({"type": "turn.completed", "usage": None}) == []


def test_events_codex_ignores_unrelated_event_types() -> None:
    assert _events_codex({"type": "turn.started"}) == []


def test_events_codex_flat_tool_call_yields_tool_intent() -> None:
    line = {"type": "tool_call", "id": "call_1", "name": "shell", "arguments": {"command": "ls"}}
    assert _events_codex(line) == [ToolIntent("call_1", "shell", {"command": "ls"})]


def test_events_codex_flat_tool_call_accepts_the_alternate_input_spelling() -> None:
    # Older Codex CLI versions wrote `input` where current ones write
    # `arguments`; real logs on disk predate the rename.
    line = {"type": "tool_call", "id": "call_1", "name": "shell", "input": {"command": "ls"}}
    assert _events_codex(line) == [ToolIntent("call_1", "shell", {"command": "ls"})]


def test_events_codex_flat_tool_call_decodes_json_encoded_arguments() -> None:
    line = {"type": "tool_call", "id": "call_1", "name": "shell", "arguments": '{"command": "ls"}'}
    assert _events_codex(line) == [ToolIntent("call_1", "shell", {"command": "ls"})]


def test_events_codex_flat_tool_call_keeps_unparseable_arguments_string() -> None:
    line = {"type": "tool_call", "id": "call_1", "name": "shell", "arguments": "not json"}
    assert _events_codex(line) == [ToolIntent("call_1", "shell", {"arguments": "not json"})]


def test_events_codex_flat_tool_call_without_id_yields_an_empty_correlation_id() -> None:
    # Explicitly NOT the renderer's behaviour, which invents a random id purely
    # to key a DOM element. A synthesised id can never match an outcome, so it
    # would fabricate a correlation that does not exist.
    line = {"type": "tool_call", "name": "shell", "arguments": {}}
    assert _events_codex(line) == [ToolIntent("", "shell", {})]


def test_events_codex_flat_tool_result_normalizes_its_correlation_field() -> None:
    # Asymmetric on purpose: the request names the id `id`, the result names it
    # `tool_call_id`. Both land on the vocabulary's single `call_id`.
    line = {"type": "tool_result", "tool_call_id": "call_1", "result": "total 0"}
    assert _events_codex(line) == [ToolOutcome("call_1", "total 0", False)]


def test_events_codex_flat_tool_result_joins_content_block_array() -> None:
    line = {
        "type": "tool_result", "tool_call_id": "call_1",
        "result": [{"text": "line one"}, {"text": "line two"}],
    }
    assert _events_codex(line) == [ToolOutcome("call_1", "line one\nline two", False)]


def test_events_codex_flat_tool_result_serializes_a_structured_object_result() -> None:
    # The third encoding: an object with no text at all. Yielding "" here would
    # silently drop a real result, so it is serialized instead.
    line = {"type": "tool_result", "tool_call_id": "call_1", "result": {"exit_code": 0, "ok": True}}
    assert _events_codex(line) == [
        ToolOutcome("call_1", '{"exit_code": 0, "ok": true}', False)
    ]


def test_events_codex_flat_tool_result_carries_error_flag() -> None:
    line = {"type": "tool_result", "tool_call_id": "call_1", "result": "boom", "is_error": True}
    assert _events_codex(line) == [ToolOutcome("call_1", "boom", True)]


# ── _events_codex: item.* lifecycle family (issue #77) ─────────────────
def test_events_codex_item_started_command_execution_yields_tool_intent() -> None:
    line = {
        "type": "item.started",
        "item": {"id": "it_1", "type": "command_execution", "command": '/bin/bash -lc "ls -la"'},
    }
    # The command is carried raw. renderer.js unwraps the /bin/bash -lc wrapper
    # for display; what actually ran is the wrapped form.
    assert _events_codex(line) == [
        ToolIntent("it_1", "Bash", {"command": '/bin/bash -lc "ls -la"'})
    ]


def test_events_codex_item_completed_command_execution_yields_intent_and_outcome() -> None:
    # A completion line can arrive with no preceding start line (the renderer
    # defends against exactly this by synthesising the start card). A pure
    # classifier cannot know whether it saw the start, so a completion always
    # carries both events and consumers dedupe on call_id.
    line = {
        "type": "item.completed",
        "item": {
            "id": "it_1", "type": "command_execution",
            "command": "ls", "aggregated_output": "README.md", "exit_code": 0,
        },
    }
    assert _events_codex(line) == [
        ToolIntent("it_1", "Bash", {"command": "ls"}),
        ToolOutcome("it_1", "README.md", False),
    ]


def test_events_codex_command_execution_nonzero_exit_code_is_an_error() -> None:
    line = {
        "type": "item.completed",
        "item": {"id": "it_1", "type": "command_execution", "command": "false",
                 "aggregated_output": "", "exit_code": 1},
    }
    assert _events_codex(line)[1] == ToolOutcome("it_1", "", True)


def test_events_codex_command_execution_absent_exit_code_is_not_an_error() -> None:
    # A zero exit code and an absent one are both non-failures, and must not be
    # conflated — `exit_code or 0` would be right for one and wrong for neither.
    line = {
        "type": "item.completed",
        "item": {"id": "it_1", "type": "command_execution", "command": "ls", "aggregated_output": "x"},
    }
    assert _events_codex(line)[1] == ToolOutcome("it_1", "x", False)


def test_events_codex_item_started_mcp_tool_call_yields_tool_intent() -> None:
    line = {
        "type": "item.started",
        "item": {"id": "it_2", "type": "mcp_tool_call", "server": "coderfleet",
                 "tool": "ask_user_question", "arguments": {"a": 1}},
    }
    assert _events_codex(line) == [ToolIntent(
        "it_2", "ask_user_question",
        {"server": "coderfleet", "tool": "ask_user_question", "arguments": {"a": 1}},
    )]


def test_events_codex_item_started_web_search_reads_a_top_level_query() -> None:
    line = {"type": "item.started", "item": {"id": "it_3", "type": "web_search", "query": "python"}}
    assert _events_codex(line) == [ToolIntent("it_3", "WebSearch", {"query": "python"})]


def test_events_codex_item_started_web_search_reads_a_nested_action_query() -> None:
    line = {
        "type": "item.started",
        "item": {"id": "it_3", "type": "web_search", "action": {"query": "python"}},
    }
    assert _events_codex(line) == [ToolIntent("it_3", "WebSearch", {"query": "python"})]


def test_events_codex_item_started_web_search_reads_the_first_of_nested_queries() -> None:
    line = {
        "type": "item.started",
        "item": {"id": "it_3", "type": "web_search", "action": {"queries": ["first", "second"]}},
    }
    assert _events_codex(line) == [ToolIntent("it_3", "WebSearch", {"query": "first"})]


def test_events_codex_item_started_collab_tool_call_yields_tool_intent() -> None:
    line = {
        "type": "item.started",
        "item": {"id": "it_4", "type": "collab_tool_call", "tool": "spawn_agent",
                 "prompt": "go", "receiver_thread_ids": ["t1"]},
    }
    assert _events_codex(line) == [ToolIntent(
        "it_4", "spawn_agent", {"tool": "spawn_agent", "prompt": "go", "agents": ["t1"]},
    )]


def test_events_codex_generic_tool_item_failure_is_determined_from_status() -> None:
    for status in ("cancelled", "error", "failed"):
        line = {
            "type": "item.completed",
            "item": {"id": "it_5", "type": "web_search", "query": "q", "status": status},
        }
        assert _events_codex(line)[1].is_error is True, status


def test_events_codex_generic_tool_item_success_status_is_not_an_error() -> None:
    line = {
        "type": "item.completed",
        "item": {"id": "it_5", "type": "mcp_tool_call", "tool": "t",
                 "status": "completed", "result": {"Ok": "fine"}},
    }
    assert _events_codex(line)[1] == ToolOutcome("it_5", '{"Ok": "fine"}', False)


def test_events_codex_todo_list_item_yields_no_tool_events_in_any_phase() -> None:
    # todo_list is a plan/progress surface, not a tool call — and it is the only
    # item type that pushes repeated snapshots through the update phase.
    item = {"id": "it_6", "type": "todo_list", "items": [{"text": "a", "completed": False}]}
    for phase in ("item.started", "item.updated", "item.completed"):
        assert _events_codex({"type": phase, "item": item}) == [], phase


def test_events_codex_item_updated_yields_nothing_for_any_item_type() -> None:
    item = {"id": "it_1", "type": "command_execution", "command": "ls"}
    assert _events_codex({"type": "item.updated", "item": item}) == []


def test_events_codex_item_completed_agent_message_text_extraction_unchanged() -> None:
    line = {"type": "item.completed", "item": {"id": "it_7", "type": "agent_message", "text": "answer"}}
    assert _events_codex(line) == [TextChunk("answer")]


# ── _events_opencode ───────────────────────────────────────────────────
def test_events_opencode_part_wrapped_text_yields_text_chunk() -> None:
    line = {"type": "text", "part": {"type": "text", "id": "p2", "text": "Final response text."}}
    assert _events_opencode(line) == [TextChunk("Final response text.")]


def test_events_opencode_part_wrapped_text_with_empty_text_yields_nothing() -> None:
    line = {"type": "text", "part": {"type": "text", "text": ""}}
    assert _events_opencode(line) == []


def test_events_opencode_step_finish_yields_combined_usage_delta_and_cost() -> None:
    line = {
        "type": "step_finish",
        "part": {
            "type": "step-finish",
            "tokens": {"input": 100, "output": 30, "reasoning": 5, "cache": {"read": 10, "write": 2}},
            "cost": 0.01,
        },
    }
    # input = tokens.input + cache.read + cache.write = 100 + 10 + 2 = 112
    # output = tokens.output + tokens.reasoning = 30 + 5 = 35
    assert _events_opencode(line) == [UsageDelta(112, 35), CostDelta(0.01)]


def test_events_opencode_step_finish_with_zero_cost_omits_cost_event() -> None:
    line = {"type": "step_finish", "part": {"type": "step-finish", "tokens": {"input": 5, "output": 1}, "cost": 0}}
    assert _events_opencode(line) == [UsageDelta(5, 1)]


def test_events_opencode_legacy_flat_message_yields_text_and_usage_progress() -> None:
    line = {
        "type": "message", "role": "assistant",
        "content": [{"type": "text", "text": "legacy answer"}],
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }
    assert _events_opencode(line) == [TextChunk("legacy answer"), UsageProgress(10, 5)]


def test_events_opencode_assistant_role_with_string_content() -> None:
    assert _events_opencode({"role": "assistant", "content": "plain string"}) == [TextChunk("plain string")]


def test_events_opencode_text_event_and_top_level_usage_both_fire_independently() -> None:
    # The text-block check and the generic top-level usage check are two
    # independent conditionals, not an if/elif chain -- a single line can
    # trigger both.
    line = {"type": "text", "part": {"type": "text", "text": "hi"}, "usage": {"input_tokens": 3, "output_tokens": 1}}
    assert _events_opencode(line) == [TextChunk("hi"), UsageProgress(3, 1)]


def test_events_opencode_ignores_unrelated_event_types() -> None:
    assert _events_opencode({"type": "step_start", "part": {"type": "step-start"}}) == []


# ── _events_opencode: tool calls (issue #78) ───────────────────────────
def _oc_tool(state: dict, call_id: str = "oc_1", tool: str = "bash") -> dict:
    return {"type": "tool_use", "part": {"type": "tool", "callID": call_id, "tool": tool, "state": state}}


def test_events_opencode_running_tool_yields_intent_only() -> None:
    line = _oc_tool({"status": "running", "input": {"command": "ls"}})
    assert _events_opencode(line) == [ToolIntent("oc_1", "bash", {"command": "ls"})]


def test_events_opencode_completed_tool_yields_intent_and_outcome() -> None:
    # opencode is the only format whose request and result arrive on the SAME
    # event type; a terminal line always carries both, because the classifier
    # cannot know whether the running line was ever written.
    line = _oc_tool({"status": "completed", "input": {"command": "ls"}, "output": "README.md"})
    assert _events_opencode(line) == [
        ToolIntent("oc_1", "bash", {"command": "ls"}),
        ToolOutcome("oc_1", "README.md", False),
    ]


def test_events_opencode_terminal_line_alone_still_yields_both_events() -> None:
    # The "terminal line only" ordering — a log where the running line was
    # never written must not lose the fact that the tool was called at all.
    line = _oc_tool({"status": "completed", "input": {}, "output": "x"})
    assert [type(e).__name__ for e in _events_opencode(line)] == ["ToolIntent", "ToolOutcome"]


def test_events_opencode_tool_id_falls_back_to_part_id() -> None:
    line = {"type": "tool_use", "part": {"type": "tool", "id": "prt_9", "tool": "read",
                                         "state": {"status": "running", "input": {}}}}
    assert _events_opencode(line) == [ToolIntent("prt_9", "read", {})]


def test_events_opencode_tool_name_falls_back_to_the_nested_state() -> None:
    line = {"type": "tool_use", "part": {"type": "tool", "callID": "oc_1",
                                         "state": {"status": "running", "tool": "grep", "input": {}}}}
    assert _events_opencode(line) == [ToolIntent("oc_1", "grep", {})]


def test_events_opencode_tool_name_is_not_canonicalised() -> None:
    # renderer.js maps bash/shell → "Bash" for display. The classifier reports
    # what the CLI actually called it — see the comment on the classifier.
    line = _oc_tool({"status": "running", "input": {}}, tool="webfetch")
    assert _events_opencode(line)[0].name == "webfetch"


def test_events_opencode_result_text_falls_back_to_metadata_output() -> None:
    line = _oc_tool({"status": "completed", "input": {}, "metadata": {"output": "from metadata"}})
    assert _events_opencode(line)[1] == ToolOutcome("oc_1", "from metadata", False)


def test_events_opencode_error_status_is_a_failure() -> None:
    for status in ("error", "failed"):
        line = _oc_tool({"status": status, "input": {}, "output": "boom"})
        assert _events_opencode(line)[1].is_error is True, status


def test_events_opencode_nonzero_metadata_exit_is_a_failure_on_its_own() -> None:
    # Two independent failure signals; either alone is sufficient. Here the
    # status says success and only the exit code says otherwise.
    line = _oc_tool({"status": "completed", "input": {}, "output": "", "metadata": {"exit": 2}})
    assert _events_opencode(line)[1].is_error is True


def test_events_opencode_zero_metadata_exit_is_not_a_failure() -> None:
    line = _oc_tool({"status": "completed", "input": {}, "output": "ok", "metadata": {"exit": 0}})
    assert _events_opencode(line)[1] == ToolOutcome("oc_1", "ok", False)


# ── registry-wide coverage claims (issue #74) ──────────────────────────
def test_hermes_registers_no_output_event_classifier() -> None:
    # hermes writes plain text, not JSONL, so it has no per-line classifier and
    # makes no coverage claim — log_parser falls straight through to its
    # plain-text extraction. Pinned by test so a future "every type should have
    # one" sweep can't quietly bolt a classifier onto a format that has no
    # lines to classify.
    assert ACCOUNT_TYPES["hermes"].parse_output_events is None


def test_default_shape_discriminator_reads_the_type_field() -> None:
    assert _shape_by_type({"type": "message_end", "message": {}}) == "message_end"
    assert _shape_by_type({"role": "assistant"}) == ""
