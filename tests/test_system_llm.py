"""Tests for the system-level LLM module and the thin translate function."""
from __future__ import annotations

import asyncio

import httpx
import pytest

from coderfleet.server.system_llm import SystemLLM, SystemLLMError
from coderfleet.server.translate import translate_text
from coderfleet.server.summarize_title import summarize_title


def _run(coro):
    return asyncio.run(coro)


def _mock(handler) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


# ── is_configured ───────────────────────────────────────────

def test_is_configured_requires_key_and_model():
    assert not SystemLLM(api_key="", model="m").is_configured()
    assert not SystemLLM(api_key="k", model="").is_configured()
    assert SystemLLM(api_key="k", model="m").is_configured()


def test_complete_unconfigured_raises():
    with pytest.raises(SystemLLMError):
        _run(SystemLLM().complete([{"role": "user", "content": "hi"}]))


# ── anthropic adapter ───────────────────────────────────────

def test_anthropic_wire_and_parse():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"] = str(req.url)
        seen["key"] = req.headers.get("x-api-key")
        seen["ver"] = req.headers.get("anthropic-version")
        import json
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"content": [
            {"type": "text", "text": "你好"}, {"type": "text", "text": "世界"},
        ]})

    llm = SystemLLM(provider="anthropic", api_key="k", model="claude-x", transport=_mock(handler))
    out = _run(llm.complete([{"role": "user", "content": "hi"}], system="be brief"))

    assert out == "你好世界"
    assert seen["url"].endswith("/v1/messages")
    assert seen["key"] == "k"
    assert seen["ver"] == "2023-06-01"
    # anthropic: system 是顶层字段，不混进 messages
    assert seen["body"]["system"] == "be brief"
    assert seen["body"]["messages"] == [{"role": "user", "content": "hi"}]


# ── openai adapter ──────────────────────────────────────────

def test_openai_wire_and_parse():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"] = str(req.url)
        seen["auth"] = req.headers.get("authorization")
        import json
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"choices": [
            {"message": {"role": "assistant", "content": "hola"}},
        ]})

    llm = SystemLLM(provider="openai", api_key="k", model="gpt-x", transport=_mock(handler))
    out = _run(llm.complete([{"role": "user", "content": "hi"}], system="be brief"))

    assert out == "hola"
    assert seen["url"].endswith("/chat/completions")
    assert seen["auth"] == "Bearer k"
    # openai: system 作为第一条 message
    assert seen["body"]["messages"][0] == {"role": "system", "content": "be brief"}
    assert seen["body"]["messages"][1] == {"role": "user", "content": "hi"}


def test_base_url_override():
    def handler(req: httpx.Request) -> httpx.Response:
        assert str(req.url).startswith("https://gw.internal/v1/chat/completions")
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    llm = SystemLLM(provider="openai", api_key="k", model="m",
                    base_url="https://gw.internal/v1", transport=_mock(handler))
    assert _run(llm.complete([{"role": "user", "content": "x"}])) == "ok"


def test_upstream_error_raises():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="unauthorized")

    llm = SystemLLM(provider="anthropic", api_key="bad", model="m", transport=_mock(handler))
    with pytest.raises(SystemLLMError) as e:
        _run(llm.complete([{"role": "user", "content": "x"}]))
    assert "401" in str(e.value)


def test_unknown_provider_raises():
    llm = SystemLLM(provider="grok", api_key="k", model="m")
    with pytest.raises(SystemLLMError):
        _run(llm.complete([{"role": "user", "content": "x"}]))


# ── translate thin function ─────────────────────────────────

def test_translate_empty_short_circuits():
    # 空文本不该发起任何请求（transport 缺失也不会被触碰）
    llm = SystemLLM(provider="anthropic", api_key="k", model="m")
    assert _run(translate_text(llm, "   ")) == ""


def test_translate_sends_target_and_text():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        import json
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"content": [{"type": "text", "text": "已翻译"}]})

    llm = SystemLLM(provider="anthropic", api_key="k", model="m", transport=_mock(handler))
    out = _run(translate_text(llm, "Hello world", target="简体中文"))

    assert out == "已翻译"
    assert "简体中文" in seen["body"]["system"]
    assert seen["body"]["messages"][0]["content"] == "Hello world"


# ── summarize_title 薄函数 ────────────────────────────────────

def test_summarize_title_empty_short_circuits():
    llm = SystemLLM(provider="anthropic", api_key="k", model="m")
    assert _run(summarize_title(llm, "   ")) == ""


def test_summarize_title_sends_prompt_and_text():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        import json
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"content": [{"type": "text", "text": "会话标题"}]})

    llm = SystemLLM(provider="anthropic", api_key="k", model="m", transport=_mock(handler))
    out = _run(summarize_title(llm, "帮我看下这段代码为什么报错"))

    assert out == "会话标题"
    assert "title" in seen["body"]["system"].lower()
    assert seen["body"]["messages"][0]["content"] == "帮我看下这段代码为什么报错"


# ── from_config ─────────────────────────────────────────────

def test_from_config_reads_keys(tmp_path):
    (tmp_path / "config.conf").write_text(
        "SYSTEM_LLM_PROVIDER=openai\n"
        "SYSTEM_LLM_API_KEY=sk-abc\n"
        "SYSTEM_LLM_MODEL=gpt-x\n"
        "SYSTEM_LLM_BASE_URL=https://gw/v1\n",
        encoding="utf-8",
    )
    llm = SystemLLM.from_config(tmp_path)
    assert llm.provider == "openai"
    assert llm.api_key == "sk-abc"
    assert llm.model == "gpt-x"
    assert llm.base_url == "https://gw/v1"
    assert llm.is_configured()
