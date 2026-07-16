"""Tests for the settings schema, config round-trip, and page wiring."""
from __future__ import annotations

from pathlib import Path

from coderfleet.config import load_config, set_config
from coderfleet.server.settings_schema import (
    SETTINGS_GROUPS, all_fields, field_for, mask_secret,
)

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "coderfleet" / "server" / "static"


# ── schema 结构 ─────────────────────────────────────────────

def test_keys_unique_and_uppercase():
    keys = [f.key for f in all_fields()]
    assert len(keys) == len(set(keys)), "配置键有重复"
    assert all(k == k.upper() for k in keys)


def test_field_for_is_case_insensitive():
    assert field_for("system_llm_api_key").key == "SYSTEM_LLM_API_KEY"
    assert field_for("SYSTEM_LLM_MODEL").label == "模型"
    assert field_for("NOPE_NOT_A_KEY") is None


def test_provider_and_platform_have_options():
    assert field_for("SYSTEM_LLM_PROVIDER").options == ("anthropic", "openai")
    assert field_for("BUILD_PLATFORM").options == ("linux/arm64", "linux/amd64")


def test_apply_flags_split_live_vs_infra():
    # system LLM 即时生效，网络/代理需 apply
    assert not field_for("SYSTEM_LLM_API_KEY").requires_apply
    assert field_for("INTERNAL_SUBNET").requires_apply
    assert field_for("PROXY_HOST").requires_apply


def test_api_key_is_secret():
    assert field_for("SYSTEM_LLM_API_KEY").secret
    assert not field_for("SYSTEM_LLM_MODEL").secret


# ── 密钥脱敏 ────────────────────────────────────────────────

def test_mask_secret_never_leaks_full_value():
    assert mask_secret("") == ""
    assert mask_secret("   ") == ""
    masked = mask_secret("sk-abcdef123456")
    assert masked.endswith("3456")
    assert "abcdef" not in masked
    assert mask_secret("ab") == "••"   # 太短则不露尾


# ── config 往返（与端点相同的读写路径） ─────────────────────

def test_config_round_trip_with_schema_keys(tmp_path):
    for f in all_fields():
        set_config(tmp_path, f.key, "x")   # 值不含空格
    cfg = load_config(tmp_path)
    for f in all_fields():
        assert cfg[f.key] == "x"


def test_schema_values_are_space_free_conf_safe(tmp_path):
    # parse_conf 以空格分词，占位符不应含空格（否则写回会被截断）
    for f in all_fields():
        assert " " not in f.key


# ── 前后端接线（源码断言，无需启动 app） ───────────────────

def test_backend_endpoints_wired():
    src = (ROOT / "coderfleet" / "server" / "main.py").read_text(encoding="utf-8")
    assert '@app.get("/api/config")' in src
    assert '@app.put("/api/config")' in src
    assert '@app.get("/api/system-llm/status")' in src
    assert '@app.post("/api/translate"' in src
    assert '@app.post("/api/summarize-title"' in src


def test_settings_page_and_entry_wired():
    index = (STATIC / "index.html").read_text(encoding="utf-8")
    assert 'data-page="settings"' in index
    assert 'id="page-settings"' in index
    assert 'settings.js' in index
    # 退出登录已从右上角移除
    assert 'top-logout-btn' not in index


def test_settings_js_and_nav_wired():
    js = (STATIC / "js" / "settings.js").read_text(encoding="utf-8")
    assert "function loadSettings" in js
    assert "function saveSettingsGroup" in js
    assert "logoutApiKey()" in js          # 退出登录迁移到设置页
    nav = (STATIC / "js" / "nav.js").read_text(encoding="utf-8")
    assert "settings: '系统设置'" in nav
    assert "loadSettings()" in nav


# ── telegram 分组 ───────────────────────────────────────────

def test_telegram_group_registered():
    group = next((g for g in SETTINGS_GROUPS if g.id == "telegram"), None)
    assert group is not None
    keys = {f.key for f in group.fields}
    assert {"TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "TELEGRAM_PROXY",
            "TELEGRAM_NOTIFY_MODE"} <= keys


def test_telegram_secrets_and_live_effect():
    assert field_for("TELEGRAM_BOT_TOKEN").secret
    assert not field_for("TELEGRAM_CHAT_ID").secret
    # Telegram 配置即时生效，不需要 coderfleet apply
    assert not field_for("TELEGRAM_BOT_TOKEN").requires_apply
    assert not field_for("TELEGRAM_NOTIFY_MODE").requires_apply
    assert field_for("TELEGRAM_NOTIFY_MODE").options == ("off", "text", "voice")


def test_telegram_topic_group_field():
    f = field_for("TELEGRAM_TOPIC_GROUP_ID")
    assert f is not None and not f.secret and not f.requires_apply
