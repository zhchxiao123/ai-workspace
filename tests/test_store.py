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


def _count_reads(monkeypatch) -> "list[int]":
    """monkeypatch Path.read_text 计数调用次数，返回一个可变的单元素列表当计数器。"""
    counter = [0]
    original = Path.read_text

    def counting_read_text(self, *args, **kwargs):
        counter[0] += 1
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counting_read_text)
    return counter


def test_all_does_not_reread_unchanged_files_on_second_call(tmp_path: Path, monkeypatch) -> None:
    # 绕过 store.save()，模拟"文件已经在磁盘上、缓存还是冷的"这个真实场景
    # （调用方每次都 new 一个 JsonStore 实例，都是从空缓存开始读已有目录）。
    (tmp_path / "a.json").write_text(json.dumps({"id": "a", "name": "alpha"}), encoding="utf-8")
    (tmp_path / "b.json").write_text(json.dumps({"id": "b", "name": "beta"}), encoding="utf-8")
    store: JsonStore[Rec] = JsonStore(Rec, tmp_path)

    counter = _count_reads(monkeypatch)
    first = store.all()
    reads_after_first = counter[0]
    assert reads_after_first > 0

    second = store.all()
    assert counter[0] == reads_after_first  # 第二次没有新增磁盘读
    assert {r.id for r in second} == {r.id for r in first}


def test_all_rereads_file_after_content_and_mtime_change(tmp_path: Path) -> None:
    store: JsonStore[Rec] = JsonStore(Rec, tmp_path)
    store.save(Rec(id="a", name="alpha"))
    store.all()  # 首次加载，写入缓存

    # 绕过 store 直接改写文件内容并把 mtime 往后拨，模拟外部/后续 save() 产生的变化
    p = tmp_path / "a.json"
    p.write_text(json.dumps({"id": "a", "name": "renamed", "n": 0}), encoding="utf-8")
    import os as _os
    _os.utime(p, (p.stat().st_atime, p.stat().st_mtime + 100))

    got = store.all()
    assert [r.name for r in got] == ["renamed"]


def test_all_picks_up_new_file_added_after_first_call(tmp_path: Path) -> None:
    store: JsonStore[Rec] = JsonStore(Rec, tmp_path)
    store.save(Rec(id="a"))
    first = store.all()
    assert {r.id for r in first} == {"a"}

    store.save(Rec(id="b"))
    second = store.all()
    assert {r.id for r in second} == {"a", "b"}


def test_all_drops_deleted_file(tmp_path: Path) -> None:
    store: JsonStore[Rec] = JsonStore(Rec, tmp_path)
    store.save(Rec(id="a"))
    store.save(Rec(id="b"))
    store.all()  # 预热缓存

    store.delete("a")
    got = store.all()
    assert {r.id for r in got} == {"b"}


def test_save_updates_cache_even_when_stat_is_unchanged(tmp_path: Path, monkeypatch) -> None:
    """粗粒度 mtime 场景：save() 不能依赖"下次 stat() 发现变化"来刷新缓存，
    必须自己把新内容直接写入缓存——否则同一秒内的两次 save 会让 all() 读到旧值。"""
    store: JsonStore[Rec] = JsonStore(Rec, tmp_path)
    store.save(Rec(id="a", name="alpha"))
    store.all()  # 预热缓存

    target = tmp_path / "a.json"
    original_stat = Path.stat
    frozen = original_stat(target)

    class FrozenStat:
        st_mtime = frozen.st_mtime
        st_size = frozen.st_size

    def fake_stat(self, *args, **kwargs):
        if self == target:
            return FrozenStat()
        return original_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", fake_stat)

    store.save(Rec(id="a", name="beta"))  # target 的 stat() 被冻结，看起来"没变"
    got = store.all()
    assert [r.name for r in got] == ["beta"]
