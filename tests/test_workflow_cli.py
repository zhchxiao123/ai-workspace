"""CLI 测试：coderfleet workflow 子命令（对应 issue #19）。"""
from __future__ import annotations

import httpx
import pytest
from click.testing import CliRunner

from coderfleet import workflow_cmds


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
    monkeypatch.setattr(workflow_cmds, "_require_server", lambda: "http://test")


def test_template_list(monkeypatch):
    payload = [{"id": "tpl-1", "name": "构建+审查", "description": "两步", "nodes": [{}, {}]}]
    monkeypatch.setattr(httpx, "get", lambda url, **kw: _FakeResp(json_data=payload))
    result = CliRunner().invoke(workflow_cmds.workflow_group, ["template", "list"])
    assert result.exit_code == 0, result.output
    assert "tpl-1" in result.output and "2 个节点" in result.output


def test_template_run_with_project_map(monkeypatch):
    captured = {}
    def fake_post(url, **kw):
        captured["json"] = kw.get("json")
        return _FakeResp(status_code=201, json_data={"id": "run-1", "status": "running"})
    monkeypatch.setattr(httpx, "post", fake_post)
    result = CliRunner().invoke(workflow_cmds.workflow_group, [
        "template", "run", "tpl-1", "--input", "做登录",
        "--project", "coder=web", "--project", "reviewer=api",
    ])
    assert result.exit_code == 0, result.output
    assert "run-1" in result.output
    assert captured["json"]["project_map"] == {"coder": "web", "reviewer": "api"}
    assert captured["json"]["input"] == "做登录"


def test_template_run_rejects_bad_project_pair(monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda url, **kw: _FakeResp(status_code=201, json_data={"id": "x", "status": "running"}))
    result = CliRunner().invoke(workflow_cmds.workflow_group, [
        "template", "run", "tpl-1", "--project", "noequalsign",
    ])
    assert result.exit_code != 0
    assert "格式错误" in result.output


def test_run_list(monkeypatch):
    payload = [{"id": "run-1", "name": "登录", "status": "running",
                "node_executions": [{"state": "succeeded"}, {"state": "running"}]}]
    monkeypatch.setattr(httpx, "get", lambda url, **kw: _FakeResp(json_data=payload))
    result = CliRunner().invoke(workflow_cmds.workflow_group, ["run", "list"])
    assert result.exit_code == 0, result.output
    assert "run-1" in result.output and "1/2" in result.output


def test_run_logs_shows_nodes(monkeypatch):
    payload = {
        "id": "run-1", "status": "failed",
        "node_executions": [
            {"node_id": "a", "name": "build", "state": "succeeded", "task_id": "t1",
             "outputs": {"text": "build ok\nmore"}},
            {"node_id": "b", "name": "test", "state": "failed", "task_id": "t2",
             "error_message": "boom"},
        ],
    }
    monkeypatch.setattr(httpx, "get", lambda url, **kw: _FakeResp(json_data=payload))
    result = CliRunner().invoke(workflow_cmds.workflow_group, ["run", "logs", "run-1"])
    assert result.exit_code == 0, result.output
    assert "build" in result.output and "成功" in result.output
    assert "失败" in result.output and "boom" in result.output
    assert "build ok" in result.output


def test_run_approve_fetches_token_then_posts(monkeypatch):
    calls = {}
    monkeypatch.setattr(httpx, "get", lambda url, **kw: _FakeResp(json_data={"id": "run-1", "approval_token": "tok-9"}))
    def fake_post(url, **kw):
        calls["params"] = kw.get("params")
        return _FakeResp(status_code=200, json_data={"id": "run-1", "status": "running"})
    monkeypatch.setattr(httpx, "post", fake_post)
    result = CliRunner().invoke(workflow_cmds.workflow_group, ["run", "approve", "run-1"])
    assert result.exit_code == 0, result.output
    assert calls["params"] == {"token": "tok-9"}
