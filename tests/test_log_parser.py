from __future__ import annotations

import json

from coderfleet.server.log_parser import extract_task_output, parse_log


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
