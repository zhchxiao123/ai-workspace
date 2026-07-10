"""CLI 测试：coderfleet board / card 子命令（对应 issue #18）。"""
from __future__ import annotations

import httpx
import pytest
from click.testing import CliRunner

from coderfleet import board_cmds


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
    monkeypatch.setattr(board_cmds, "_require_server", lambda: "http://test")


def test_board_list_shows_boards_and_columns(monkeypatch):
    def fake_get(url, **kw):
        if url.endswith("/api/boards"):
            return _FakeResp(json_data=[{"id": "board-1", "name": "开发专题"}])
        if "/cards" in url:
            return _FakeResp(json_data=[
                {"id": "bc-1", "title": "登录", "status": "running", "task_ids": ["t1", "t2"]},
                {"id": "bc-2", "title": "支付", "status": "blocked", "task_ids": []},
            ])
        return _FakeResp(json_data=[])
    monkeypatch.setattr(httpx, "get", fake_get)
    result = CliRunner().invoke(board_cmds.board_group, ["list"])
    assert result.exit_code == 0, result.output
    assert "board-1" in result.output
    assert "开发专题" in result.output
    assert "受阻 1" in result.output
    assert "bc-1" in result.output and "登录" in result.output


def test_board_list_empty(monkeypatch):
    monkeypatch.setattr(httpx, "get", lambda url, **kw: _FakeResp(json_data=[]))
    result = CliRunner().invoke(board_cmds.board_group, ["list"])
    assert result.exit_code == 0
    assert "暂无看板" in result.output


def test_card_add(monkeypatch):
    resp = _FakeResp(status_code=201, json_data={"id": "bc-9", "title": "新专题", "status": "planned"})
    monkeypatch.setattr(httpx, "post", lambda url, **kw: resp)
    result = CliRunner().invoke(board_cmds.card_group, ["add", "board-1", "新专题", "--project", "web"])
    assert result.exit_code == 0, result.output
    assert "bc-9" in result.output


def test_card_add_board_not_found(monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda url, **kw: _FakeResp(status_code=404, json_data={"detail": "看板 'x' 不存在"}))
    result = CliRunner().invoke(board_cmds.card_group, ["add", "x", "标题"])
    assert result.exit_code != 0
    assert "不存在" in result.output


def test_card_move(monkeypatch):
    monkeypatch.setattr(httpx, "patch", lambda url, **kw: _FakeResp(status_code=200, json_data={"id": "bc-1", "status": "blocked"}))
    result = CliRunner().invoke(board_cmds.card_group, ["move", "bc-1", "blocked"])
    assert result.exit_code == 0, result.output
    assert "受阻" in result.output


def test_card_move_rejects_bad_status(monkeypatch):
    result = CliRunner().invoke(board_cmds.card_group, ["move", "bc-1", "nonsense"])
    assert result.exit_code != 0  # click.Choice 校验拦截
