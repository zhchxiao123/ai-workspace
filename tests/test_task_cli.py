"""CLI 测试：coderfleet task answer（Intervention 回答，见 issue #69）。"""
from __future__ import annotations

import httpx
import pytest
from click.testing import CliRunner

from coderfleet import task_cmds


class _FakeResp:
    def __init__(self, status_code=200, json_data=None, text="", content_type="application/json"):
        self.status_code = status_code
        self._json = json_data if json_data is not None else {}
        self.text = text
        self.headers = {"content-type": content_type}

    def json(self):
        return self._json


@pytest.fixture(autouse=True)
def _no_server(monkeypatch):
    monkeypatch.setattr(task_cmds, "_require_server", lambda: "http://test")


def test_task_answer_fetches_token_then_posts(monkeypatch):
    monkeypatch.setattr(
        httpx, "get",
        lambda url, **kw: _FakeResp(json_data={
            "id": "task-1",
            "pending_intervention": {"tool_call_id": "tc-1", "token": "tok-9", "questions": []},
        }),
    )
    calls = {}

    def fake_post(url, **kw):
        calls["json"] = kw.get("json")
        return _FakeResp(status_code=200, json_data={"ok": True})

    monkeypatch.setattr(httpx, "post", fake_post)

    result = CliRunner().invoke(
        task_cmds.task_group, ["answer", "task-1", "--json", '{"which color?": "red"}'],
    )

    assert result.exit_code == 0, result.output
    assert calls["json"] == {
        "tool_call_id": "tc-1", "token": "tok-9", "answers": {"which color?": "red"},
    }


def test_task_answer_rejects_invalid_json(monkeypatch):
    result = CliRunner().invoke(
        task_cmds.task_group, ["answer", "task-1", "--json", "not-json"],
    )
    assert result.exit_code != 0
    assert "JSON" in result.output


def test_task_answer_errors_when_no_pending_question(monkeypatch):
    monkeypatch.setattr(
        httpx, "get",
        lambda url, **kw: _FakeResp(json_data={"id": "task-1", "pending_intervention": None}),
    )
    result = CliRunner().invoke(
        task_cmds.task_group, ["answer", "task-1", "--json", "{}"],
    )
    assert result.exit_code != 0
    assert "没有待回答的问题" in result.output


def test_task_status_shows_pending_intervention(monkeypatch):
    monkeypatch.setattr(
        httpx, "get",
        lambda url, **kw: _FakeResp(json_data={
            "id": "task-1", "status": "running", "account": "alice", "type": "claude",
            "project": "/srv/x", "created": "2026-01-01T00:00:00+00:00",
            "prompt": "do the thing",
            "pending_intervention": {
                "tool_call_id": "tc-1", "token": "tok-9",
                "questions": [{"question": "which color?", "header": "", "multiSelect": False, "options": []}],
            },
        }),
    )
    result = CliRunner().invoke(task_cmds.task_group, ["status", "task-1"])
    assert result.exit_code == 0, result.output
    assert "which color?" in result.output


def test_task_status_omits_intervention_section_when_none_pending(monkeypatch):
    monkeypatch.setattr(
        httpx, "get",
        lambda url, **kw: _FakeResp(json_data={
            "id": "task-1", "status": "done", "account": "alice", "type": "claude",
            "project": "/srv/x", "created": "2026-01-01T00:00:00+00:00",
            "prompt": "do the thing", "pending_intervention": None,
        }),
    )
    result = CliRunner().invoke(task_cmds.task_group, ["status", "task-1"])
    assert result.exit_code == 0, result.output
    assert "待回答" not in result.output


def test_task_answer_surfaces_409_conflict(monkeypatch):
    monkeypatch.setattr(
        httpx, "get",
        lambda url, **kw: _FakeResp(json_data={
            "id": "task-1",
            "pending_intervention": {"tool_call_id": "tc-1", "token": "stale-token", "questions": []},
        }),
    )
    monkeypatch.setattr(
        httpx, "post",
        lambda url, **kw: _FakeResp(status_code=409, json_data={"detail": "token 不匹配"}),
    )
    result = CliRunner().invoke(
        task_cmds.task_group, ["answer", "task-1", "--json", "{}"],
    )
    assert result.exit_code != 0
    assert "token 不匹配" in result.output
