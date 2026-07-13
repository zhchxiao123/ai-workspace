from __future__ import annotations

from coderfleet.server.log_parser import extract_task_output


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
