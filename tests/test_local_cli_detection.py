"""test_local_cli_detection.py — 宿主机 CLI 自动探测（scenario 3）。

CLI（`coderfleet account detect`）和 Web UI（`/api/accounts/detect-local`）共用
`detect_local_cli`/`detect_all_local_clis`，这里只测这一个函数本身；探测只回答
"二进制存不存在、版本是什么"，不做登录态检查。
"""
from __future__ import annotations

import asyncio
import subprocess

import pytest

from pathlib import Path

from fastapi import HTTPException

from coderfleet.account_type_registry import (
    detect_all_local_clis,
    detect_local_cli,
    valid_type_ids,
)
from coderfleet.server import main as server_main
from coderfleet.server.models import AccountAuth, AccountProxy, AccountRuntime, AccountType
from coderfleet.server.scheduler import Scheduler


def test_detect_local_cli_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda _binary: None)

    result = detect_local_cli("claude")

    assert result.found is False
    assert result.binary == "claude"
    assert result.path == ""
    assert result.version == ""


def test_detect_local_cli_found_parses_version(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda _binary: "/usr/bin/claude")

    def fake_run(argv, **kwargs):
        assert argv == ["/usr/bin/claude", "--version"]
        return subprocess.CompletedProcess(argv, 0, stdout="2.1.220 (Claude Code)\n", stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)

    result = detect_local_cli("claude")

    assert result.found is True
    assert result.path == "/usr/bin/claude"
    assert result.version == "2.1.220 (Claude Code)"


def test_detect_local_cli_found_but_version_probe_fails_does_not_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("shutil.which", lambda _binary: "/usr/bin/codex")

    def boom(argv, **kwargs):
        raise TimeoutError("probe timed out")

    monkeypatch.setattr("subprocess.run", boom)

    result = detect_local_cli("codex")

    assert result.found is True
    assert result.path == "/usr/bin/codex"
    assert result.version == ""  # 探测版本失败不影响"已安装"这个事实


def test_detect_all_local_clis_covers_every_registered_type(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda _binary: None)

    results = detect_all_local_clis()

    assert {r.type_id for r in results} == set(valid_type_ids())
    assert all(r.found is False for r in results)


# ── /api/accounts/detect-local：Web UI 与 CLI 共用同一个探测函数 ──────────
def test_detect_local_clis_endpoint_reuses_registry_function(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda binary: "/usr/bin/claude" if binary == "claude" else None)

    def fake_run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, stdout="2.1.220 (Claude Code)\n", stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)

    results = asyncio.run(server_main.detect_local_clis())

    by_type = {r.type_id: r for r in results}
    assert by_type["claude"].found is True
    assert by_type["claude"].version == "2.1.220 (Claude Code)"
    assert by_type["codex"].found is False
    assert {r.type_id for r in results} == set(valid_type_ids())


# ── POST /api/accounts：local+codex+relay 在 API 层也被拒绝（422） ──────
def test_create_account_endpoint_rejects_local_codex_relay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from coderfleet.server.main import AccountCreateRequest

    ws = tmp_path / "workspace"
    ws.mkdir()
    monkeypatch.setattr(server_main, "scheduler", Scheduler(ws))

    req = AccountCreateRequest(
        name="bob", type=AccountType.codex, auth=AccountAuth.login,
        proxy=AccountProxy.relay, runtime=AccountRuntime.local,
    )
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(server_main.create_account(req))

    assert exc_info.value.status_code == 422
    assert "openai/codex#4242" in exc_info.value.detail


def test_create_account_endpoint_persists_sandbox_confirmed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from coderfleet.server.main import AccountCreateRequest

    ws = tmp_path / "workspace"
    ws.mkdir()
    monkeypatch.setattr(server_main, "scheduler", Scheduler(ws))

    req = AccountCreateRequest(
        name="alice", type=AccountType.claude, auth=AccountAuth.login,
        proxy=AccountProxy.relay, runtime=AccountRuntime.local, sandbox_confirmed=True,
    )
    asyncio.run(server_main.create_account(req))

    acc = next(a for a in server_main.scheduler.get_accounts() if a.name == "alice")
    assert acc.sandbox_confirmed is True
