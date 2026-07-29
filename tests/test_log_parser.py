from __future__ import annotations

import json
from dataclasses import replace
from unittest.mock import patch

from coderfleet.account_type_registry import ACCOUNT_TYPES, TextChunk
from coderfleet.server.log_parser import (
    ShapeCoverage,
    detect_unrecognized_shapes,
    extract_task_output,
    parse_log,
    split_complete_lines,
)


def test_extract_kimi_stream_json_output() -> None:
    log = "\n".join([
        '{"role":"assistant","content":"I will inspect the repo."}',
        '{"role":"tool","tool_call_id":"tc_1","content":"README.md"}',
        '{"role":"assistant","content":"Done."}',
        '{"role":"meta","type":"session.resume_hint","session_id":"ses_123"}',
    ])

    assert extract_task_output(log, "kimi") == "I will inspect the repo.Done."


def test_extract_claude_assistant_usage_maxes_input_and_sums_output() -> None:
    # Characterization test: claude parsing had no direct coverage in this
    # file before this PRD's refactor. Captures today's real behavior so the
    # upcoming account_type_registry.py-driven rewrite has a byte-identical
    # regression proof, not just an aspiration.
    log = "\n".join([
        json.dumps({
            "type": "assistant",
            "message": {
                "content": [{"type": "text", "text": "Hello"}],
                "usage": {"input_tokens": 50, "output_tokens": 10},
            },
        }),
        json.dumps({
            "type": "assistant",
            "message": {
                "content": [{"type": "text", "text": " there"}],
                "usage": {"input_tokens": 30, "output_tokens": 5},
            },
        }),
        json.dumps({"type": "usage", "input_tokens": 5, "output_tokens": 2, "cost_usd": 0.01}),
        json.dumps({"type": "result", "result": "Hello there", "cost_usd": 0.02, "session_id": "s1"}),
    ])

    result = parse_log(log, "claude")
    assert result.text == "Hello there"
    # input_tokens is a running max across assistant turns (50, then 30 -> 50
    # stays), then the standalone "usage" event's 5 is additive on top.
    assert result.tokens_input == 55
    # output_tokens is additive across every reading: 10 + 5 + 2.
    assert result.tokens_output == 17
    # The "result" event's cost_usd always overwrites, unlike the "usage"
    # event's cost_usd which only sets if not already set.
    assert result.cost_usd == 0.02


def test_extract_claude_falls_back_to_accumulated_text_without_result_event() -> None:
    # No explicit "result" event (e.g. a killed/truncated task) -- final text
    # is reconstructed from accumulated assistant text chunks instead.
    log = "\n".join([
        json.dumps({
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "partial"}]},
        }),
    ])

    assert extract_task_output(log, "claude") == "partial"


def test_extract_grok_streaming_text_data_output() -> None:
    log = "\n".join([
        '{"type":"text","data":"ta"}',
        '{"type":"text","data":"uri"}',
        '{"type":"text","data":" &&"}',
        '{"type":"text","data":" cargo"}',
        '{"type":"text","data":" test"}',
        '{"type":"text","data":"\\n\\n"}',
        '{"type":"text","data":"##"}',
        '{"type":"text","data":" 总结"}',
        '{"type":"text","data":"判断"}',
        '{"type":"text","data":"\\n\\n"}',
        '{"type":"text","data":"这是"}',
        '{"type":"text","data":"一个"}',
        '{"type":"text","data":" MVP"}',
        '{"type":"end","stopReason":"EndTurn","sessionId":"019f5abf-0185-7840-b426-038c29bd87be"}',
    ])

    assert extract_task_output(log, "grok") == (
        "tauri && cargo test\n\n"
        "## 总结判断\n\n"
        "这是一个 MVP"
    )


def test_extract_grok_output_ignores_thought_tokens() -> None:
    log = "\n".join([
        '{"type":"thought","data":"The"}',
        '{"type":"thought","data":" user"}',
        '{"type":"thought","data":" wants"}',
        '{"type":"text","data":"我"}',
        '{"type":"text","data":"先"}',
        '{"type":"text","data":"从"}',
        '{"type":"text","data":"仓库"}',
        '{"type":"text","data":"结构"}',
        '{"type":"text","data":"入手"}',
        '{"type":"end","stopReason":"EndTurn","sessionId":"019f5abf-0185-7840-b426-038c29bd87be"}',
    ])

    assert extract_task_output(log, "grok") == "我先从仓库结构入手"


def test_extract_codex_item_completed_agent_message_text() -> None:
    # Shape from `codex exec --json` (what account_type_registry.py actually
    # invokes) -- final text arrives on item.completed/agent_message, not on
    # a top-level "message" event.
    log = "\n".join([
        json.dumps({"type": "thread.started", "thread_id": "abc123"}),
        json.dumps({"type": "turn.started"}),
        json.dumps({"type": "item.started", "item": {"id": "item_1", "type": "agent_message"}}),
        json.dumps({
            "type": "item.completed",
            "item": {"id": "item_1", "type": "agent_message", "text": "Here is the final answer."},
        }),
        json.dumps({"type": "turn.completed", "usage": {"input_tokens": 120, "output_tokens": 45}}),
    ])

    result = parse_log(log, "codex")
    assert result.text == "Here is the final answer."
    assert result.tokens_input == 120
    assert result.tokens_output == 45


def test_extract_codex_turn_completed_usage_is_cumulative_not_summed() -> None:
    # turn.completed's usage is a running total for the whole thread, not a
    # per-turn delta -- a second turn.completed must overwrite, not add to,
    # the first, or usage would be double-counted for multi-turn threads.
    log = "\n".join([
        json.dumps({"type": "turn.completed", "usage": {"input_tokens": 100, "output_tokens": 20}}),
        json.dumps({"type": "turn.completed", "usage": {"input_tokens": 150, "output_tokens": 35}}),
    ])

    result = parse_log(log, "codex")
    assert result.tokens_input == 150
    assert result.tokens_output == 35


def test_extract_codex_legacy_message_role_still_works() -> None:
    # Older codex JSON schema some historical logs may still carry.
    log = json.dumps({
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": "legacy answer"}],
    })

    assert extract_task_output(log, "codex") == "legacy answer"


def test_extract_opencode_part_wrapped_text_and_tokens() -> None:
    # Current OpenCode CLI nests everything under `part` (matches
    # renderer.js's _opencodeText/_opencodeStepFinish) -- previously the
    # parser only looked at flat top-level fields and silently fell back to
    # a raw JSONL dump for every opencode task.
    log = "\n".join([
        json.dumps({"type": "step_start", "sessionID": "sess1", "part": {"type": "step-start", "id": "p1"}}),
        json.dumps({"type": "text", "part": {"type": "text", "id": "p2", "text": "Final response text."}}),
        json.dumps({
            "type": "step_finish",
            "part": {
                "type": "step-finish", "id": "p3",
                "tokens": {"input": 100, "output": 30, "reasoning": 5, "cache": {"read": 10, "write": 2}},
                "cost": 0.01,
            },
        }),
    ])

    result = parse_log(log, "opencode")
    assert result.text == "Final response text."
    assert result.tokens_input == 112  # input + cache.read + cache.write
    assert result.tokens_output == 35  # output + reasoning
    assert result.cost_usd == 0.01


def test_extract_opencode_legacy_flat_shape_still_works() -> None:
    log = json.dumps({
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": "legacy answer"}],
        "usage": {"input_tokens": 10, "output_tokens": 5},
    })

    result = parse_log(log, "opencode")
    assert result.text == "legacy answer"
    assert result.tokens_input == 10
    assert result.tokens_output == 5


def _pi_message(role, content, usage=None):
    msg = {"role": role, "content": content}
    if usage is not None:
        msg["usage"] = usage
    return msg


def test_extract_pi_final_answer_ignores_thinking_blocks() -> None:
    # Shape taken from a real `pi --mode json` transcript.
    user_msg = _pi_message("user", [{"type": "text", "text": "hello"}])
    assistant_msg = _pi_message(
        "assistant",
        [
            {"type": "thinking", "thinking": "The user just said \"hello\". No tool calls needed."},
            {"type": "text", "text": "Hello! \U0001f44b How can I help you today?"},
        ],
        usage={
            "input": 781, "output": 59, "cacheRead": 0, "cacheWrite": 0,
            "totalTokens": 840,
            "cost": {"input": 0.01, "output": 0.02, "cacheRead": 0, "cacheWrite": 0, "total": 0.03},
        },
    )
    log = "\n".join([
        json.dumps({"type": "session", "version": 3, "id": "019f7b78-22a3-78c8-a20d-0acb8d31edc1", "timestamp": "2026-07-19T17:41:38.595Z", "cwd": "/workspace"}),
        json.dumps({"type": "agent_start"}),
        json.dumps({"type": "turn_start"}),
        json.dumps({"type": "message_start", "message": user_msg}),
        json.dumps({"type": "message_end", "message": user_msg}),
        json.dumps({"type": "message_end", "message": assistant_msg}),
        json.dumps({"type": "turn_end", "message": assistant_msg}),
        json.dumps({"type": "agent_end", "messages": [user_msg, assistant_msg], "willRetry": False}),
        json.dumps({"type": "agent_settled"}),
    ])

    assert extract_task_output(log, "pi") == "Hello! \U0001f44b How can I help you today?"


def test_extract_pi_token_usage_and_cost_from_message_end() -> None:
    assistant_msg = _pi_message(
        "assistant",
        [{"type": "text", "text": "Done."}],
        usage={
            "input": 100, "output": 20, "cacheRead": 5, "cacheWrite": 0,
            "totalTokens": 120,
            "cost": {"input": 0.001, "output": 0.002, "cacheRead": 0, "cacheWrite": 0, "total": 0.003},
        },
    )
    log = "\n".join([
        json.dumps({"type": "message_end", "message": assistant_msg}),
        json.dumps({"type": "agent_end", "messages": [assistant_msg], "willRetry": False}),
    ])

    result = parse_log(log, "pi")
    assert result.text == "Done."
    assert result.tokens_input == 100
    assert result.tokens_output == 20
    assert result.cost_usd == 0.003


def test_extract_pi_falls_back_to_last_assistant_message_when_no_agent_end() -> None:
    # A killed/truncated task may never emit agent_end.
    assistant_msg = _pi_message("assistant", [{"type": "text", "text": "partial answer"}])
    log = json.dumps({"type": "message_end", "message": assistant_msg})

    assert extract_task_output(log, "pi") == "partial answer"


def _pi_tool_lines() -> list[str]:
    return [
        json.dumps({
            "type": "tool_execution_start",
            "toolCallId": "c1", "toolName": "Bash", "args": {"command": "ls"},
        }),
        json.dumps({
            "type": "tool_execution_end",
            "toolCallId": "c1",
            "result": {"content": [{"text": "README.md"}]},
        }),
    ]


def test_tool_events_do_not_perturb_text_or_usage() -> None:
    # ToolIntent/ToolOutcome carry information TaskOutputData has no field for
    # (issue #74). Interleaving them with the shapes that *do* fold must leave
    # text/token/cost byte-identical — the fold's no-op branches are explicit
    # precisely so this stays true rather than depending on the if/elif chain
    # falling off its end.
    assistant_msg = _pi_message(
        "assistant",
        [{"type": "text", "text": "Done."}],
        usage={
            "input": 100, "output": 20,
            "cost": {"total": 0.003},
        },
    )
    tool_lines = _pi_tool_lines()
    without_tools = json.dumps({"type": "message_end", "message": assistant_msg})
    with_tools = "\n".join(tool_lines[:1] + [without_tools] + tool_lines[1:])

    assert parse_log(with_tools, "pi") == parse_log(without_tools, "pi")


# ── coverage detector (issue #74) ─────────────────────────────────────────────

def test_detect_unrecognized_shapes_empty_for_fully_classified_log() -> None:
    assistant_msg = _pi_message("assistant", [{"type": "text", "text": "Done."}])
    log = "\n".join(
        _pi_tool_lines() + [json.dumps({"type": "message_end", "message": assistant_msg})]
    )

    assert detect_unrecognized_shapes(log, "pi") == ShapeCoverage("pi", {})


def test_detect_unrecognized_shapes_ignores_declared_silent_shapes() -> None:
    log = "\n".join([
        json.dumps({"type": "session", "id": "s1"}),
        json.dumps({"type": "agent_start"}),
        json.dumps({"type": "turn_start"}),
        json.dumps({"type": "agent_settled"}),
    ])

    assert detect_unrecognized_shapes(log, "pi") == ShapeCoverage("pi", {})


def test_detect_unrecognized_shapes_reports_an_undeclared_shape() -> None:
    log = "\n".join([
        json.dumps({"type": "turn_start"}),
        json.dumps({"type": "quantum_leap", "payload": 1}),
        json.dumps({"type": "quantum_leap", "payload": 2}),
    ])

    report = detect_unrecognized_shapes(log, "pi")
    assert report == ShapeCoverage("pi", {"quantum_leap": 2})
    # A report is meant to be actionable on sight: it has to say which account
    # type it is about, since the shape name alone is ambiguous across types.
    assert "pi" in str(report) and "quantum_leap" in str(report)


def test_detect_unrecognized_shapes_spares_a_shape_that_classified_elsewhere() -> None:
    # pi emits `message_end` for user turns too; those legitimately produce no
    # events while the assistant ones do. Silence is a property of the shape
    # across the whole log, not of one line, so this must stay quiet.
    user_msg = _pi_message("user", [{"type": "text", "text": "hi"}])
    assistant_msg = _pi_message("assistant", [{"type": "text", "text": "Done."}])
    log = "\n".join([
        json.dumps({"type": "message_end", "message": user_msg}),
        json.dumps({"type": "message_end", "message": assistant_msg}),
    ])

    assert detect_unrecognized_shapes(log, "pi") == ShapeCoverage("pi", {})


def test_detect_unrecognized_shapes_honours_a_non_type_discriminator() -> None:
    # The load-bearing half of the registry design: a type that names its line
    # shapes on something other than `type` must be expressible **without
    # touching the detector**. kimi is the real such type (it discriminates on
    # `role`) and is a later slice of #71; this proves the mechanism now, since
    # discovering it doesn't generalise only when kimi lands would mean
    # reworking every classifier slice in between.
    spec = replace(
        ACCOUNT_TYPES["pi"],
        shape_of=lambda d: str(d.get("role", "")),
        silent_shapes=frozenset({"system"}),
        parse_output_events=lambda d: [TextChunk("x")] if d.get("role") == "assistant" else [],
    )
    log = "\n".join([
        json.dumps({"role": "assistant", "content": "hi"}),
        json.dumps({"role": "system", "content": "banner"}),
        json.dumps({"role": "tool", "content": "output"}),
    ])

    with patch.dict(ACCOUNT_TYPES, {"pi": spec}):
        report = detect_unrecognized_shapes(log, "pi")

    # `assistant` classified, `system` declared silent, `tool` neither — and
    # every one of those verdicts was reached by reading `role`, which the
    # detector itself never mentions.
    assert report == ShapeCoverage("pi", {"tool": 1})


def test_detect_unrecognized_shapes_clean_on_a_realistic_full_claude_log() -> None:
    log = "\n".join([
        json.dumps({"type": "system", "subtype": "init", "model": "claude-opus-5", "tools": ["Read"]}),
        json.dumps({"type": "assistant", "message": {"content": [
            {"type": "text", "text": "Let me look."},
            {"type": "tool_use", "id": "toolu_1", "name": "Read", "input": {"file_path": "/a"}},
        ], "usage": {"input_tokens": 50, "output_tokens": 10}}}),
        json.dumps({"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "toolu_1", "content": "file body"},
        ]}}),
        json.dumps({"type": "rate_limit_event", "rate_limit_info": {"status": "allowed"}}),
        json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "Done."}]}}),
        json.dumps({"type": "result", "result": "Done.", "cost_usd": 0.02, "session_id": "s1"}),
    ])

    assert detect_unrecognized_shapes(log, "claude") == ShapeCoverage("claude", {})


def test_detect_unrecognized_shapes_clean_on_a_realistic_full_codex_log() -> None:
    # Carries both of codex's parallel tool representations: the flat
    # tool_call/tool_result pair and the item.* lifecycle family.
    log = "\n".join([
        json.dumps({"type": "thread.started", "thread_id": "th_1"}),
        json.dumps({"type": "turn.started"}),
        json.dumps({"type": "reasoning", "text": "thinking"}),
        json.dumps({"type": "tool_call", "id": "call_1", "name": "shell", "arguments": {"command": "ls"}}),
        json.dumps({"type": "tool_result", "tool_call_id": "call_1", "result": "README.md"}),
        json.dumps({"type": "item.started", "item": {"id": "it_1", "type": "command_execution", "command": "ls"}}),
        json.dumps({"type": "item.updated", "item": {"id": "it_2", "type": "todo_list", "items": []}}),
        json.dumps({"type": "item.completed", "item": {
            "id": "it_1", "type": "command_execution", "command": "ls",
            "aggregated_output": "README.md", "exit_code": 0,
        }}),
        json.dumps({"type": "item.completed", "item": {
            "id": "it_3", "type": "agent_message", "text": "Done.",
        }}),
        json.dumps({"type": "turn.completed", "usage": {"input_tokens": 150, "output_tokens": 35}}),
        json.dumps({"type": "turn.ended"}),
        json.dumps({"type": "thread.ended", "result": "ok"}),
    ])

    assert detect_unrecognized_shapes(log, "codex") == ShapeCoverage("codex", {})


def test_detect_unrecognized_shapes_clean_on_a_realistic_full_opencode_log() -> None:
    log = "\n".join([
        json.dumps({"type": "step_start", "part": {"type": "step-start"}}),
        json.dumps({"type": "reasoning", "part": {"type": "reasoning", "text": "thinking"}}),
        json.dumps({"type": "tool_use", "part": {
            "type": "tool", "callID": "oc_1", "tool": "bash",
            "state": {"status": "running", "input": {"command": "ls"}},
        }}),
        json.dumps({"type": "tool_use", "part": {
            "type": "tool", "callID": "oc_1", "tool": "bash",
            "state": {"status": "completed", "input": {"command": "ls"},
                      "output": "README.md", "metadata": {"exit": 0}},
        }}),
        json.dumps({"type": "text", "part": {"type": "text", "text": "Done."}}),
        json.dumps({"type": "step_finish", "part": {
            "type": "step-finish", "tokens": {"input": 100, "output": 30}, "cost": 0.01,
        }}),
    ])

    assert detect_unrecognized_shapes(log, "opencode") == ShapeCoverage("opencode", {})


def test_detect_unrecognized_shapes_empty_for_type_without_classifier() -> None:
    # hermes is plain text, not JSONL — it has no classifier, so there is no
    # coverage claim to check and nothing to report.
    assert detect_unrecognized_shapes('{"type":"whatever"}', "hermes") == ShapeCoverage("hermes", {})


def test_detect_unrecognized_shapes_empty_for_unknown_account_type() -> None:
    # Same graceful fall-through parse_log() has for a legacy/malformed
    # acc_type on an old Task record: report nothing, never raise.
    assert detect_unrecognized_shapes('{"type":"whatever"}', "no-such-type") == ShapeCoverage(
        "no-such-type", {}
    )


def test_detect_unrecognized_shapes_reports_a_line_whose_classifier_blew_up() -> None:
    # A CLI retyping a field (object → string, int → string) is a normal form of
    # the very drift this detector exists to catch, and the classifiers assume
    # field types freely. If a raising line propagated, the detector would take
    # down its caller with a stack trace on exactly its motivating input — so a
    # line its classifier could not survive counts as unclassified, not as fatal.
    log = "\n".join([
        json.dumps({"type": "message_end", "message": "not an object"}),
        json.dumps({"type": "message_end", "message": "still not an object"}),
    ])

    assert detect_unrecognized_shapes(log, "pi") == ShapeCoverage("pi", {"message_end": 2})


def test_detect_unrecognized_shapes_clean_on_a_realistic_full_pi_log() -> None:
    # The detector's whole point is that a real end-to-end transcript comes out
    # silent — otherwise nobody would ever read its output. This is the same
    # log shape asserted in test_extract_pi_final_answer_ignores_thinking_blocks,
    # plus a tool call.
    user_msg = _pi_message("user", [{"type": "text", "text": "hello"}])
    assistant_msg = _pi_message(
        "assistant",
        [{"type": "text", "text": "Hi."}],
        usage={"input": 10, "output": 2, "cost": {"total": 0.001}},
    )
    log = "\n".join([
        json.dumps({"type": "session", "version": 3, "id": "s1", "cwd": "/workspace"}),
        json.dumps({"type": "agent_start"}),
        json.dumps({"type": "turn_start"}),
        json.dumps({"type": "message_start", "message": user_msg}),
        json.dumps({"type": "message_end", "message": user_msg}),
    ] + _pi_tool_lines() + [
        json.dumps({"type": "message_update", "message": assistant_msg}),
        json.dumps({"type": "message_end", "message": assistant_msg}),
        json.dumps({"type": "turn_end", "message": assistant_msg}),
        json.dumps({"type": "agent_end", "messages": [user_msg, assistant_msg]}),
        json.dumps({"type": "agent_settled"}),
    ])

    assert detect_unrecognized_shapes(log, "pi").unrecognized == {}


def test_split_complete_lines_holds_back_partial_tail() -> None:
    complete, pending = split_complete_lines(b'{"a":1}\n{"b":2')
    assert complete == b'{"a":1}\n'
    assert pending == b'{"b":2'


def test_split_complete_lines_no_newline_yet() -> None:
    complete, pending = split_complete_lines(b'{"a":1')
    assert complete == b""
    assert pending == b'{"a":1'


def test_split_complete_lines_everything_complete() -> None:
    complete, pending = split_complete_lines(b'{"a":1}\n{"b":2}\n')
    assert complete == b'{"a":1}\n{"b":2}\n'
    assert pending == b""


def test_split_complete_lines_reassembles_across_calls() -> None:
    # Simulates the actual failure mode: a poll tick lands mid-write, splitting
    # one JSON line's bytes across two reads. The caller must hold back the
    # partial tail and prepend it to the next chunk before re-checking.
    first_read = b'{"type":"user","message":{"content":[{"tool_use_id":"t1","content":"fn f() {'
    second_read = b'\\n}"}]}}\n'

    complete1, pending = split_complete_lines(first_read)
    assert complete1 == b""  # no newline yet — nothing safe to flush

    complete2, pending = split_complete_lines(pending + second_read)
    assert pending == b""
    assert json.loads(complete2.decode())["message"]["content"][0]["content"] == "fn f() {\n}"
