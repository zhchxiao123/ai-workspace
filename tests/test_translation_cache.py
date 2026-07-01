"""Tests for the content-addressed translation cache."""
from __future__ import annotations

from coderfleet.server.translation_cache import TranslationCache, _key


def test_put_then_get_round_trip(tmp_path):
    c = TranslationCache(tmp_path)
    assert c.get("Hello world", "简体中文") is None
    c.put("Hello world", "简体中文", "你好，世界")
    assert c.get("Hello world", "简体中文") == "你好，世界"


def test_key_depends_on_text_and_target():
    assert _key("a", "简体中文") != _key("b", "简体中文")   # 文本不同
    assert _key("a", "简体中文") != _key("a", "English")    # 目标语不同
    assert _key("a", "zh") == _key("a", "zh")               # 稳定


def test_target_isolates_entries(tmp_path):
    c = TranslationCache(tmp_path)
    c.put("cat", "简体中文", "猫")
    c.put("cat", "English", "cat")
    assert c.get("cat", "简体中文") == "猫"
    assert c.get("cat", "English") == "cat"


def test_survives_new_instance_same_dir(tmp_path):
    """抗刷新：新实例（等价于服务重启/换端）仍能读到旧译文。"""
    TranslationCache(tmp_path).put("Reload me", "简体中文", "重新加载我")
    assert TranslationCache(tmp_path).get("Reload me", "简体中文") == "重新加载我"


def test_source_preview_truncated(tmp_path):
    c = TranslationCache(tmp_path)
    long_text = "x" * 500
    c.put(long_text, "简体中文", "译")
    rec = c._store.get(_key(long_text, "简体中文"))
    assert rec is not None
    assert len(rec.source_preview) == 80
    assert rec.created  # 打了时间戳


def test_identical_text_dedups_to_one_file(tmp_path):
    c = TranslationCache(tmp_path)
    c.put("same", "简体中文", "一样")
    c.put("same", "简体中文", "一样")   # 覆盖同键，不新增文件
    assert len(list(tmp_path.glob("*.json"))) == 1
