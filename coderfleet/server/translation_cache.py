"""
translation_cache.py — 气泡翻译的内容寻址缓存

同一段原文永远对应同一段译文，所以按 hash(目标语言 + 原文) 落盘缓存：
  - 抗刷新 / 抗气泡重建（键不依赖易变的 DOM 或 task id，只依赖内容）
  - 天然去重：多处出现的同一段文本只翻一次、全局复用
  - 永不失效：内容没变，键就没变；换模型不入键，避免整仓译文作废

深度藏在 get/put 两个方法后面：hash、文件布局、原子落盘都是实现细节，
复用 store.py 的 JsonStore（先写临时文件再 os.replace）。
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from pydantic import BaseModel

from coderfleet.server.models import now_iso
from coderfleet.server.store import JsonStore


class TranslationRecord(BaseModel):
    id: str                  # sha256(target \0 text) 前 32 位
    target: str
    translated: str
    source_preview: str = ""
    created: str = ""


def _key(text: str, target: str) -> str:
    # \0 分隔，避免 target 与 text 边界歧义；不含 model，换模型不使缓存失效
    digest = hashlib.sha256(f"{target}\x00{text}".encode("utf-8")).hexdigest()
    return digest[:32]


class TranslationCache:
    def __init__(self, directory: Path) -> None:
        self._store: JsonStore[TranslationRecord] = JsonStore(TranslationRecord, directory)

    def get(self, text: str, target: str) -> str | None:
        rec = self._store.get(_key(text, target))
        return rec.translated if rec else None

    def put(self, text: str, target: str, translated: str) -> None:
        self._store.save(TranslationRecord(
            id=_key(text, target),
            target=target,
            translated=translated,
            source_preview=text[:80],
            created=now_iso(),
        ))
