from __future__ import annotations

import asyncio
import json
import subprocess

import pytest

from coderfleet import usage_probe


def _fake_claude_stdout(status: int, body: dict, subscription: str = "max") -> str:
    return json.dumps({"status": status, "body": json.dumps(body), "subscription": subscription}) + "\n"


def _fake_codex_stdout(status: int, body: dict) -> str:
    return json.dumps({"status": status, "body": json.dumps(body)}) + "\n"


# ── Claude ───────────────────────────────────────────────────

def test_parse_claude_probe_stdout_no_credentials() -> None:
    stdout = json.dumps({"error": "no_credentials"}) + "\n"
    usage = usage_probe._parse_claude_probe_stdout(stdout, "now")
    assert usage.error == "no_credentials"


def test_parse_claude_probe_stdout_network_error() -> None:
    stdout = json.dumps({"error": "network_error", "detail": "boom"}) + "\n"
    usage = usage_probe._parse_claude_probe_stdout(stdout, "now")
    assert usage.error == "network_error"


def test_parse_claude_probe_stdout_empty() -> None:
    usage = usage_probe._parse_claude_probe_stdout("", "now")
    assert usage.error == "empty_response"


def test_parse_claude_probe_stdout_garbage() -> None:
    usage = usage_probe._parse_claude_probe_stdout("not json at all", "now")
    assert usage.error == "invalid_json"


@pytest.mark.parametrize(
    ("status_code", "expected_error"),
    [(401, "unauthorized"), (429, "rate_limited"), (500, "http_500")],
)
def test_parse_claude_probe_stdout_error_status_codes(status_code: int, expected_error: str) -> None:
    stdout = _fake_claude_stdout(status_code, {})
    usage = usage_probe._parse_claude_probe_stdout(stdout, "now")
    assert usage.error == expected_error


def test_parse_claude_probe_stdout_success() -> None:
    body = {
        "five_hour": {"utilization": 42.0, "resets_at": "2026-07-08T10:00:00Z"},
        "seven_day": {"utilization": 18.5, "resets_at": "2026-07-15T00:00:00Z"},
        "seven_day_opus": None,
    }
    stdout = _fake_claude_stdout(200, body, subscription="max")
    usage = usage_probe._parse_claude_probe_stdout(stdout, "now")
    assert usage.error == ""
    assert usage.five_hour.utilization == 42.0
    assert usage.five_hour.resets_at == "2026-07-08T10:00:00Z"
    assert usage.seven_day.utilization == 18.5
    assert usage.seven_day_opus is None
    assert usage.subscription_type == "max"


def test_render_claude_probe_script_embeds_expected_values() -> None:
    script = usage_probe._render_claude_probe_script("9.9.9")
    assert usage_probe.CRED_PATH_IN_CONTAINER in script
    assert usage_probe.USAGE_URL in script
    assert usage_probe.OAUTH_BETA_HEADER in script
    assert "claude-code/9.9.9" in script
    compile(script, "<probe-script>", "exec")  # must be valid python


def test_probe_via_container_success(monkeypatch: pytest.MonkeyPatch) -> None:
    body = {"five_hour": {"utilization": 7.0, "resets_at": "2026-07-08T10:00:00Z"}}

    def fake_run(argv, capture_output, text, timeout):
        assert argv[:3] == ["docker", "exec", "acc-container"]
        return subprocess.CompletedProcess(argv, 0, stdout=_fake_claude_stdout(200, body), stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    usage = usage_probe.probe_via_container("acc-container")
    assert usage.error == ""
    assert usage.five_hour.utilization == 7.0


def test_probe_via_container_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(*a, **k):
        raise subprocess.TimeoutExpired(cmd="docker", timeout=25)

    monkeypatch.setattr(subprocess, "run", fake_run)
    usage = usage_probe.probe_via_container("acc-container")
    assert usage.error == "probe_timeout"


def test_probe_via_container_docker_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(*a, **k):
        raise FileNotFoundError("docker not found")

    monkeypatch.setattr(subprocess, "run", fake_run)
    usage = usage_probe.probe_via_container("acc-container")
    assert usage.error.startswith("docker_exec_error")


def test_probe_via_container_async_success(monkeypatch: pytest.MonkeyPatch) -> None:
    body = {"seven_day": {"utilization": 3.0, "resets_at": None}}

    class _FakeProc:
        async def communicate(self):
            return (_fake_claude_stdout(200, body).encode(), b"")

    async def fake_create_subprocess_exec(*args, **kwargs):
        assert args[:3] == ("docker", "exec", "acc-container")
        return _FakeProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    usage = asyncio.run(usage_probe.probe_via_container_async("acc-container"))
    assert usage.error == ""
    assert usage.seven_day.utilization == 3.0


def test_probe_via_container_async_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeProc:
        async def communicate(self):
            await asyncio.sleep(10)
            return (b"", b"")

    async def fake_create_subprocess_exec(*args, **kwargs):
        return _FakeProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr(usage_probe, "PROBE_TIMEOUT_SECONDS", 0.05)
    usage = asyncio.run(usage_probe.probe_via_container_async("acc-container"))
    assert usage.error == "probe_timeout"


# ── Codex ────────────────────────────────────────────────────

def test_parse_codex_probe_stdout_no_credentials() -> None:
    stdout = json.dumps({"error": "no_credentials"}) + "\n"
    usage = usage_probe._parse_codex_probe_stdout(stdout, "now")
    assert usage.error == "no_credentials"


@pytest.mark.parametrize(
    ("status_code", "expected_error"),
    [(401, "unauthorized"), (403, "unauthorized"), (429, "rate_limited"), (500, "http_500")],
)
def test_parse_codex_probe_stdout_error_status_codes(status_code: int, expected_error: str) -> None:
    stdout = _fake_codex_stdout(status_code, {})
    usage = usage_probe._parse_codex_probe_stdout(stdout, "now")
    assert usage.error == expected_error


def test_parse_codex_probe_stdout_success() -> None:
    body = {
        "plan_type": "plus",
        "rate_limit": {
            "primary_window": {"used_percent": 37, "reset_at": 1799600000, "limit_window_seconds": 18000},
            "secondary_window": {"used_percent": 12, "reset_at": 1800100000, "limit_window_seconds": 604800},
        },
    }
    stdout = _fake_codex_stdout(200, body)
    usage = usage_probe._parse_codex_probe_stdout(stdout, "now")
    assert usage.error == ""
    assert usage.five_hour.utilization == 37
    assert usage.seven_day.utilization == 12
    assert usage.subscription_type == "plus"
    # reset_at is epoch seconds -> must come back as a parseable ISO timestamp
    from datetime import datetime
    assert datetime.fromisoformat(usage.five_hour.resets_at).timestamp() == 1799600000


def test_parse_codex_probe_stdout_missing_windows() -> None:
    stdout = _fake_codex_stdout(200, {"plan_type": "pro", "rate_limit": {}})
    usage = usage_probe._parse_codex_probe_stdout(stdout, "now")
    assert usage.error == ""
    assert usage.five_hour is None
    assert usage.seven_day is None
    assert usage.subscription_type == "pro"


def test_render_codex_probe_script_embeds_expected_values() -> None:
    script = usage_probe._render_codex_probe_script("1.2.3")
    assert usage_probe.CODEX_CRED_PATH_IN_CONTAINER in script
    assert usage_probe.CODEX_USAGE_URL in script
    assert "codex_cli_rs/1.2.3" in script
    assert "ChatGPT-Account-Id" in script
    compile(script, "<probe-script>", "exec")


def test_probe_codex_via_container_success(monkeypatch: pytest.MonkeyPatch) -> None:
    body = {
        "plan_type": "plus",
        "rate_limit": {"primary_window": {"used_percent": 50, "reset_at": 1799600000, "limit_window_seconds": 18000}},
    }

    def fake_run(argv, capture_output, text, timeout):
        assert argv[:3] == ["docker", "exec", "codex-container"]
        return subprocess.CompletedProcess(argv, 0, stdout=_fake_codex_stdout(200, body), stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    usage = usage_probe.probe_codex_via_container("codex-container")
    assert usage.error == ""
    assert usage.five_hour.utilization == 50
    assert usage.subscription_type == "plus"


def test_probe_codex_via_container_async_success(monkeypatch: pytest.MonkeyPatch) -> None:
    body = {"plan_type": "pro", "rate_limit": {"secondary_window": {"used_percent": 8, "reset_at": 1800000000, "limit_window_seconds": 604800}}}

    class _FakeProc:
        async def communicate(self):
            return (_fake_codex_stdout(200, body).encode(), b"")

    async def fake_create_subprocess_exec(*args, **kwargs):
        assert args[:3] == ("docker", "exec", "codex-container")
        return _FakeProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    usage = asyncio.run(usage_probe.probe_codex_via_container_async("codex-container"))
    assert usage.error == ""
    assert usage.seven_day.utilization == 8
    assert usage.subscription_type == "pro"


def test_probe_codex_via_container_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(*a, **k):
        raise subprocess.TimeoutExpired(cmd="docker", timeout=25)

    monkeypatch.setattr(subprocess, "run", fake_run)
    usage = usage_probe.probe_codex_via_container("codex-container")
    assert usage.error == "probe_timeout"
