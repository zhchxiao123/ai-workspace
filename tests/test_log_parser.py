from __future__ import annotations

import json

from coderfleet.server.log_parser import extract_task_output, parse_log, split_complete_lines


def test_extract_kimi_stream_json_output() -> None:
    log = "\n".join([
        '{"role":"assistant","content":"I will inspect the repo."}',
        '{"role":"tool","tool_call_id":"tc_1","content":"README.md"}',
        '{"role":"assistant","content":"Done."}',
        '{"role":"meta","type":"session.resume_hint","session_id":"ses_123"}',
    ])

    assert extract_task_output(log, "kimi") == "I will inspect the repo.Done."


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
