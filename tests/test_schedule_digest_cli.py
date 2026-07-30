"""CLI 测试：coderfleet schedule / digest 子命令（对应 issue #12）。

用 CliRunner + httpx 打桩，验证命令正确调用 API 并渲染输出，无需真实 server。
"""
from __future__ import annotations

import httpx
import pytest
from click.testing import CliRunner

from coderfleet import schedule_cmds


class _FakeResp:
    def __init__(self, status_code=200, json_data=None, text="", content_type="application/json"):
        self.status_code = status_code
        self._json = json_data if json_data is not None else {}
        self.text = text
        self.headers = {"content-type": content_type}

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("err", request=None, response=None)


@pytest.fixture(autouse=True)
def _no_server(monkeypatch):
    # 绕过健康检查
    monkeypatch.setattr(schedule_cmds, "_require_server", lambda: "http://test")


def test_schedule_list_renders_rows(monkeypatch):
    payload = [
        {
            "id": "sched-1", "name": "每日构建", "enabled": True,
            "schedule_type": "daily", "time_of_day": "09:30",
            "target_type": "task", "project_name": "repo",
            "next_run_at": "2026-07-11T09:30:00", "last_run_at": None,
        }
    ]
    monkeypatch.setattr(httpx, "get", lambda url, **kw: _FakeResp(json_data=payload))
    result = CliRunner().invoke(schedule_cmds.schedule_group, ["list"])
    assert result.exit_code == 0, result.output
    assert "sched-1" in result.output
    assert "每日构建" in result.output
    assert "每天 09:30" in result.output
    assert "从未" in result.output  # last_run_at None


def test_schedule_list_flags_agent_created_schedules(monkeypatch):
    payload = [
        {
            "id": "sched-1", "name": "每日构建", "enabled": True,
            "schedule_type": "daily", "time_of_day": "09:30",
            "target_type": "task", "project_name": "repo",
            "next_run_at": "2026-07-11T09:30:00", "last_run_at": None,
            "created_by": "agent",
        },
        {
            "id": "sched-2", "name": "人工建的", "enabled": True,
            "schedule_type": "daily", "time_of_day": "10:00",
            "target_type": "task", "project_name": "repo",
            "next_run_at": "2026-07-11T10:00:00", "last_run_at": None,
            "created_by": "human",
        },
    ]
    monkeypatch.setattr(httpx, "get", lambda url, **kw: _FakeResp(json_data=payload))
    result = CliRunner().invoke(schedule_cmds.schedule_group, ["list"])
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    sched1_idx = next(i for i, l in enumerate(lines) if "sched-1" in l)
    sched2_idx = next(i for i, l in enumerate(lines) if "sched-2" in l)
    assert "Agent 自建" in lines[sched1_idx]
    assert "Agent 自建" not in lines[sched2_idx]


def test_schedule_list_empty(monkeypatch):
    monkeypatch.setattr(httpx, "get", lambda url, **kw: _FakeResp(json_data=[]))
    result = CliRunner().invoke(schedule_cmds.schedule_group, ["list"])
    assert result.exit_code == 0
    assert "暂无定时计划" in result.output


def test_schedule_run_triggers_task(monkeypatch):
    resp = _FakeResp(status_code=201, json_data={"run_type": "task", "task": {"id": "task-9"}})
    monkeypatch.setattr(httpx, "post", lambda url, **kw: resp)
    result = CliRunner().invoke(schedule_cmds.schedule_group, ["run", "sched-1"])
    assert result.exit_code == 0, result.output
    assert "task-9" in result.output


def test_schedule_run_not_found(monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda url, **kw: _FakeResp(status_code=404, json_data={"detail": "x"}))
    result = CliRunner().invoke(schedule_cmds.schedule_group, ["run", "missing"])
    assert result.exit_code != 0
    assert "不存在" in result.output


def test_digest_renders_stats_and_summary(monkeypatch):
    payload = {
        "date": "2026-07-09", "status": "ready",
        "total_done": 5, "total_failed": 1, "total_killed": 0,
        "tokens_input": 100, "tokens_output": 200, "cost_usd": 0.1234,
        "projects": [{"project_name": "repo", "done": 5, "failed": 1, "killed": 0}],
        "ai_summary": "今天完成了 5 个任务。",
    }
    monkeypatch.setattr(httpx, "get", lambda url, **kw: _FakeResp(json_data=payload))
    result = CliRunner().invoke(schedule_cmds.cmd_digest, ["2026-07-09"])
    assert result.exit_code == 0, result.output
    assert "完成 5" in result.output
    assert "0.1234" in result.output
    assert "今天完成了 5 个任务。" in result.output


def test_digest_empty_day(monkeypatch):
    payload = {"date": "2026-07-09", "status": "pending", "total_done": 0, "total_failed": 0, "total_killed": 0}
    monkeypatch.setattr(httpx, "get", lambda url, **kw: _FakeResp(json_data=payload))
    result = CliRunner().invoke(schedule_cmds.cmd_digest, ["2026-07-09"])
    assert result.exit_code == 0
    assert "无任务活动" in result.output
