"""
store.py — JSON 记录持久化 seam

CoderFleet 把 Task / Conversation / Board / Pipeline / WorkflowRun … 各自存成一个目录下的
``<id>.json``。迁移前，每个模型都各抄一份完全相同的 save / load / load_all：mkdir、
``write_text(json.dumps(...))``、``glob("*.json")`` 按 mtime 倒序、逐个 try/except 跳过损坏文件。

本模块把这套逻辑收拢成一个深模块 ``JsonStore``。模型只描述 schema，落盘细节藏在这里，
并且——写入改为「先写临时文件再 os.replace」的原子落盘，堵住迁移前的隐患：
非原子的 ``write_text`` 一旦在写一半时崩溃，会留下半截 JSON，而 load_all 又静默跳过损坏
文件，等于悄无声息地丢记录。

删除测试：删掉 JsonStore，8 份一模一样的 save/load/all 会在各模型里重新长出来 ——
复杂度会重新聚集，说明这个 seam 是挣来的。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


def _atomic_write_json(path: Path, data: object) -> None:
    """原子落盘：写到同目录临时文件后 os.replace（同一文件系统上是原子操作）。"""
    text = json.dumps(data, ensure_ascii=False, indent=2)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


class JsonStore(Generic[T]):
    """一个目录下、以某字段为文件名的 JSON 记录集合。

    ``key`` 指定用作文件名的模型字段（默认 ``id``；DailyDigest 用 ``date``）。
    """

    def __init__(self, model: type[T], directory: Path, *, key: str = "id") -> None:
        self._model = model
        self._dir = directory
        self._key = key

    def _path(self, name: str) -> Path:
        return self._dir / f"{name}.json"

    # ── 写 ────────────────────────────────────────────────────────────
    def save(self, record: T) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        _atomic_write_json(self._path(getattr(record, self._key)), record.model_dump())

    def delete(self, name: str) -> None:
        self._path(name).unlink(missing_ok=True)

    # ── 读 ────────────────────────────────────────────────────────────
    def load(self, path: Path) -> T:
        return self._model(**json.loads(path.read_text(encoding="utf-8")))

    def get(self, name: str) -> T | None:
        path = self._path(name)
        if not path.exists():
            return None
        try:
            return self.load(path)
        except Exception:
            return None

    def exists(self, name: str) -> bool:
        return self._path(name).exists()

    def all(self) -> list[T]:
        """目录下所有记录，按 mtime 倒序；损坏文件静默跳过。"""
        if not self._dir.exists():
            return []
        out: list[T] = []
        for p in sorted(self._dir.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
            try:
                out.append(self.load(p))
            except Exception:
                pass
        return out
