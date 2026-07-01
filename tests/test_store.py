"""test_store.py — JsonStore 持久化 seam 的单元测试。"""
from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

from coderfleet.server.store import JsonStore


class Rec(BaseModel):
    id: str
    name: str = ""
    n: int = 0


class Dated(BaseModel):
    date: str
    total: int = 0


def test_save_get_roundtrip(tmp_path: Path) -> None:
    store: JsonStore[Rec] = JsonStore(Rec, tmp_path)
    store.save(Rec(id="a", name="alpha", n=1))
    got = store.get("a")
    assert got is not None and got.name == "alpha" and got.n == 1
    assert store.exists("a") is True
    assert store.get("missing") is None
    assert store.exists("missing") is False


def test_all_sorted_by_mtime_desc(tmp_path: Path) -> None:
    store: JsonStore[Rec] = JsonStore(Rec, tmp_path)
    store.save(Rec(id="first"))
    store.save(Rec(id="second"))
    # 手动把 second 的 mtime 调新，确保它排在前面
    p = tmp_path / "second.json"
    import os as _os
    _os.utime(p, (p.stat().st_atime, p.stat().st_mtime + 100))
    ids = [r.id for r in store.all()]
    assert ids[0] == "second"
    assert set(ids) == {"first", "second"}


def test_all_skips_corrupt_files(tmp_path: Path) -> None:
    store: JsonStore[Rec] = JsonStore(Rec, tmp_path)
    store.save(Rec(id="good"))
    (tmp_path / "broken.json").write_text("{ this is not json", encoding="utf-8")
    ids = [r.id for r in store.all()]
    assert ids == ["good"]


def test_all_empty_when_dir_absent(tmp_path: Path) -> None:
    store: JsonStore[Rec] = JsonStore(Rec, tmp_path / "nope")
    assert store.all() == []


def test_delete(tmp_path: Path) -> None:
    store: JsonStore[Rec] = JsonStore(Rec, tmp_path)
    store.save(Rec(id="x"))
    store.delete("x")
    assert store.exists("x") is False
    store.delete("x")  # 幂等，不抛


def test_custom_key(tmp_path: Path) -> None:
    store: JsonStore[Dated] = JsonStore(Dated, tmp_path, key="date")
    store.save(Dated(date="2026-07-01", total=5))
    assert (tmp_path / "2026-07-01.json").exists()
    assert store.get("2026-07-01").total == 5


def test_write_is_atomic_no_tmp_left_and_valid_json(tmp_path: Path) -> None:
    store: JsonStore[Rec] = JsonStore(Rec, tmp_path)
    store.save(Rec(id="a", name="alpha"))
    # 落盘后没有临时文件残留
    assert list(tmp_path.glob(".*.tmp")) == []
    # 内容是合法 JSON
    data = json.loads((tmp_path / "a.json").read_text(encoding="utf-8"))
    assert data["name"] == "alpha"
