"""test_account_type_registry.py — pi 账号类型：命令构建 + session-id 提取（纯函数，无 I/O）。"""
from __future__ import annotations

import json

from coderfleet.account_type_registry import ACCOUNT_TYPES, _build_pi, _extract_pi


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


def test_build_pi_appends_image_paths_as_text_hint() -> None:
    cmd = _build_pi("hi", False, "t1", "marker1", "t1", "", ["/tmp/a.png"], "")
    assert "Attached images" in cmd
    assert "/tmp/a.png" in cmd


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
