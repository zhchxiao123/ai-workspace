"""
search.py — 全局搜索深模块

迁移前，/api/search 处理器里塞了 138 行业务逻辑：打分、片段截取、项目路径归属、读任务
日志。这些都不该活在一个 HTTP 请求里 —— 逻辑漏过了 HTTP 这道 seam，导致只能起整个 app
做集成测试。

这里把「给定若干项目/对话/任务，按 query 排出结果」抽成一个深模块。接口是一个函数
``search_records(...)``；日志读取以回调 ``read_log`` 注入（accept dependencies, don't
create them），所以测试只需喂普通对象、断言排序，无需 HTTP、无需真实文件系统。

结果类型（SearchMatch/SearchResult/SearchResponse）由本模块持有 —— 搜索的产物归搜索。
"""
from __future__ import annotations

from typing import Callable, Optional

from pydantic import BaseModel


# ── 结果类型 ────────────────────────────────────────────────────────────
class SearchMatch(BaseModel):
    field:   str
    snippet: str


class SearchResult(BaseModel):
    type:            str
    id:              str
    title:           str
    subtitle:        str = ""
    project_name:    str = ""
    project:         str = ""
    conversation_id: str = ""
    task_id:         str = ""
    status:          str = ""
    updated:         str = ""
    score:           int = 0
    matches:         list[SearchMatch] = []


class SearchResponse(BaseModel):
    query:   str
    results: list[SearchResult]


SCOPES = {"all", "projects", "conversations", "tasks", "content"}


# ── 打分 / 归属 / 片段 ──────────────────────────────────────────────────
def contains_project_path(project_path: str, record_path: str) -> bool:
    base = str(project_path or "").rstrip("/")
    target = str(record_path or "").rstrip("/")
    return bool(base) and (target == base or target.startswith(base + "/"))


def project_for_record(projects: list, project_name: str = "", project_path: str = ""):
    if project_name:
        found = next((p for p in projects if p.name == project_name), None)
        if found:
            return found
    return next((p for p in projects if contains_project_path(p.path, project_path)), None)


def record_project_name(projects: list, name: str = "", path: str = "") -> str:
    project = project_for_record(projects, name, path)
    return project.name if project else name


def search_snippet(text: str, query: str, radius: int = 48) -> str:
    source = str(text or "")
    q = query.lower()
    idx = source.lower().find(q)
    if idx < 0:
        return source[: radius * 2].strip()
    start = max(0, idx - radius)
    end = min(len(source), idx + len(query) + radius)
    prefix = "..." if start else ""
    suffix = "..." if end < len(source) else ""
    return (prefix + source[start:end].strip() + suffix).replace("\n", " ")


def field_match_score(text: str, query: str, base: int) -> int:
    value = str(text or "").lower()
    q = query.lower()
    if not value or q not in value:
        return 0
    if value == q:
        return base + 80
    if value.startswith(q):
        return base + 40
    return base


def collect_matches(fields: list[tuple[str, str, int]], query: str) -> tuple[int, list[SearchMatch]]:
    score = 0
    matches: list[SearchMatch] = []
    for field, text, weight in fields:
        field_score = field_match_score(text, query, weight)
        if not field_score:
            continue
        score += field_score
        matches.append(SearchMatch(field=field, snippet=search_snippet(text, query)))
    return score, matches


def status_boost(status: str) -> int:
    return 12 if status in {"running", "pending", "scheduled"} else 0


# ── 主入口 ──────────────────────────────────────────────────────────────
def search_records(
    query: str,
    scope: str,
    *,
    projects: list,
    conversations: list,
    tasks: list,
    project_name: str = "",
    deep: bool = False,
    read_log: Optional[Callable[[str], Optional[str]]] = None,
    limit: int = 50,
) -> list[SearchResult]:
    """在已取好的项目/对话/任务上做搜索，返回按分数与更新时间倒序的结果。

    ``read_log(task_id) -> str | None`` 由调用方注入；深度内容搜索通过它读取日志文本。
    """
    query = query.strip()
    if not query:
        return []

    # 完整项目列表：归属判定与 project_by_name 都用它（即便按 project_name 过滤后）。
    all_projects = list(projects)
    project_by_name = {p.name: p for p in all_projects}

    if project_name:
        projects = [p for p in projects if p.name == project_name]
        conversations = [
            c for c in conversations
            if record_project_name(all_projects, c.project_name, c.project) == project_name
        ]
        tasks = [
            t for t in tasks
            if record_project_name(all_projects, t.project_name, t.project) == project_name
        ]

    results: list[SearchResult] = []

    if scope in {"all", "projects"}:
        for project in projects:
            score, matches = collect_matches([
                ("项目名", project.name, 120),
                ("路径", project.path, 55),
                ("账号", project.account, 40),
            ], query)
            if score:
                results.append(SearchResult(
                    type="project", id=project.name, title=project.name,
                    subtitle=f"{project.path} · {project.account}",
                    project_name=project.name, project=project.path,
                    score=score, matches=matches,
                ))

    if scope in {"all", "conversations"}:
        for conv in conversations:
            project = project_by_name.get(conv.project_name) or project_for_record(all_projects, "", conv.project)
            pname = project.name if project else conv.project_name
            score, matches = collect_matches([
                ("对话名", conv.name, 120),
                ("对话 ID", conv.id, 70),
                ("项目名", pname, 60),
                ("项目路径", conv.project, 45),
                ("账号", conv.account, 35),
                ("原生会话", conv.native_session_id, 25),
            ], query)
            if score:
                results.append(SearchResult(
                    type="conversation", id=conv.id, title=conv.name or conv.id,
                    subtitle=" · ".join(v for v in [pname, conv.account, conv.status.value] if v),
                    project_name=pname or "", project=conv.project, conversation_id=conv.id,
                    status=conv.status.value, updated=conv.updated or conv.created,
                    score=score, matches=matches,
                ))

    if scope in {"all", "tasks"}:
        for task in tasks:
            project = project_by_name.get(task.project_name) or project_for_record(all_projects, "", task.project)
            pname = project.name if project else task.project_name
            score, matches = collect_matches([
                ("任务描述", task.prompt, 120),
                ("任务 ID", task.id, 70),
                ("对话 ID", task.conversation_id, 50),
                ("项目名", pname, 60),
                ("项目路径", task.project, 45),
                ("账号", task.account, 35),
                ("状态", task.status.value, 30),
            ], query)
            if score:
                score += status_boost(task.status.value)
                results.append(SearchResult(
                    type="task", id=task.id, title=task.prompt or task.id,
                    subtitle=" · ".join(v for v in [pname, task.account, task.status.value] if v),
                    project_name=pname or "", project=task.project,
                    conversation_id=task.conversation_id, task_id=task.id,
                    status=task.status.value, updated=task.finished or task.created,
                    score=score, matches=matches,
                ))

    if deep and read_log is not None and scope in {"all", "content"}:
        for task in tasks:
            text = read_log(task.id)
            if not text or query.lower() not in text.lower():
                continue
            project = project_by_name.get(task.project_name) or project_for_record(all_projects, "", task.project)
            pname = project.name if project else task.project_name
            results.append(SearchResult(
                type="content", id=f"log-{task.id}", title=task.prompt or task.id,
                subtitle=" · ".join(v for v in [pname, task.status.value, f"{task.id}.log"] if v),
                project_name=pname or "", project=task.project,
                conversation_id=task.conversation_id, task_id=task.id,
                status=task.status.value, updated=task.finished or task.created,
                score=35 + status_boost(task.status.value),
                matches=[SearchMatch(field="日志", snippet=search_snippet(text, query))],
            ))

    results.sort(key=lambda r: (r.score, r.updated), reverse=True)
    return results[:limit]
