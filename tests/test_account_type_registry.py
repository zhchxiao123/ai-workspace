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


def test_events_kimi_ignores_non_assistant_role() -> None:
    assert _events_kimi({"role": "tool", "content": "README.md"}) == []


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


def test_events_codex_item_completed_ignores_non_agent_message_items() -> None:
    line = {"type": "item.completed", "item": {"id": "item_1", "type": "command_execution"}}
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
