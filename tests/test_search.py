"""test_search.py — 全局搜索深模块（通过接口测，无 HTTP、无文件系统）。"""
from __future__ import annotations

from coderfleet.server.models import (
    Account,
    AccountType,
    Conversation,
    Project,
    Task,
    TaskStatus,
)
from coderfleet.server.search import search_records


def _project(name: str, path: str = "/srv/x", account: str = "alice") -> Project:
    return Project(name=name, account=account, path=path)


def _conv(id: str, name: str, project="/srv/x", project_name="web") -> Conversation:
    return Conversation(id=id, name=name, account="alice", type=AccountType.claude,
                        project=project, project_name=project_name)


def _task(id: str, prompt: str, status=TaskStatus.done, project="/srv/x", project_name="web") -> Task:
    return Task(id=id, status=status, account="alice", type=AccountType.claude,
                prompt=prompt, project=project, project_name=project_name)


def test_empty_query_returns_nothing() -> None:
    assert search_records("  ", "all", projects=[_project("web")], conversations=[], tasks=[]) == []


def test_project_match_by_name() -> None:
    res = search_records("web", "all", projects=[_project("web"), _project("api")],
                         conversations=[], tasks=[])
    assert [r.type for r in res] == ["project"]
    assert res[0].id == "web" and res[0].score > 0


def test_scope_restricts_types() -> None:
    res = search_records(
        "web", "projects",
        projects=[_project("web")],
        conversations=[_conv("c1", "web chat")],
        tasks=[_task("t1", "fix web bug")],
    )
    assert {r.type for r in res} == {"project"}  # 只返回项目


def test_task_running_gets_status_boost_and_orders_first() -> None:
    res = search_records(
        "deploy", "tasks", projects=[_project("web")], conversations=[],
        tasks=[
            _task("t-done", "deploy done", status=TaskStatus.done),
            _task("t-run", "deploy now", status=TaskStatus.running),
        ],
    )
    # 相同基础匹配下，running 因 status_boost 排更前
    assert res[0].id == "t-run"
    assert res[0].score > res[1].score


def test_deep_content_search_uses_injected_read_log() -> None:
    tasks = [_task("t1", "unrelated prompt")]
    logs = {"t1": "line one\nSECRET token here\nline three"}

    # deep=False → 不搜内容
    shallow = search_records("SECRET", "content", projects=[], conversations=[], tasks=tasks,
                             deep=False, read_log=lambda tid: logs.get(tid))
    assert shallow == []

    # deep=True + read_log 命中
    deep = search_records("SECRET", "content", projects=[], conversations=[], tasks=tasks,
                          deep=True, read_log=lambda tid: logs.get(tid))
    assert [r.type for r in deep] == ["content"]
    assert "SECRET" in deep[0].matches[0].snippet


def test_deep_content_skips_when_read_log_returns_none() -> None:
    res = search_records("SECRET", "content", projects=[], conversations=[],
                         tasks=[_task("t1", "x")], deep=True, read_log=lambda tid: None)
    assert res == []


def test_project_name_filter_restricts_results() -> None:
    res = search_records(
        "alice", "all",
        projects=[_project("web", account="alice"), _project("api", account="alice")],
        conversations=[], tasks=[],
        project_name="web",
    )
    assert {r.project_name for r in res} == {"web"}


def test_limit_is_respected() -> None:
    tasks = [_task(f"t{i}", "match me") for i in range(10)]
    res = search_records("match", "tasks", projects=[], conversations=[], tasks=tasks, limit=3)
    assert len(res) == 3
