"""
main.py — CoderFleet 调度服务入口

API 路由：
  GET  /api/accounts              列出所有账号及状态
  POST /api/tasks                 提交任务
  GET  /api/tasks                 列出所有任务
  GET  /api/tasks/{id}            查看任务详情
  DELETE /api/tasks/{id}          终止任务
  GET  /api/tasks/{id}/logs       获取完整日志（文本）
  GET  /api/tasks/{id}/logs/stream  SSE 实时日志流
  POST /api/tasks/clean           清理旧任务记录
  GET  /api/health                健康检查
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import AsyncIterator, Optional
from urllib.parse import urlparse

import uuid

import aiofiles
from fastapi import FastAPI, File, HTTPException, Query, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from coderfleet.server.models import (
    Account,
    AccountAuth,
    AccountProxy,
    AccountResponse,
    AccountType,
    BoardCardResponse,
    BoardCardStatus,
    BoardResponse,
    Conversation,
    ConversationResponse,
    ConversationStatus,
    LogicalProject,
    MarketplaceInstallRequest,
    Pipeline,
    PipelineResponse,
    ProjectResponse,
    Schedule,
    ScheduleCreateRequest,
    ScheduleResponse,
    ScheduleType,
    ScheduleUpdateRequest,
    Skill,
    SkillUpsertRequest,
    Task,
    TaskCreateRequest,
    TaskResponse,
    TaskStatus,
    TemplateCreateRequest,
    TemplateRunRequest,
    WorkflowTemplateResponse,
)
from coderfleet.server.auth import AuthMiddleware, load_api_key
from coderfleet.server.marketplace import MarketplaceManager
from coderfleet.server.scheduler import Scheduler
from coderfleet.server.terminal import TerminalSession, resolve_terminal_target
from coderfleet.server.push_manager import PushManager
from coderfleet.account_type_registry import ACCOUNT_TYPES


class ConversationCreateRequest(BaseModel):
    """从已有任务创建任务链的请求体"""
    name:    str
    task_id: str  # 用该任务的 native_session_id 初始化任务链


class AccountCreateRequest(BaseModel):
    name:  str
    type:  AccountType
    auth:  AccountAuth  = AccountAuth.login
    proxy: AccountProxy = AccountProxy.relay


class AccountUpdateRequest(BaseModel):
    type:  Optional[AccountType]  = None
    auth:  Optional[AccountAuth]  = None
    proxy: Optional[AccountProxy] = None


class EnvVarsRequest(BaseModel):
    vars: dict[str, str]


class ProjectCreateRequest(BaseModel):
    name:        str
    account:     str
    path:        str
    active:      bool = True
    ide_enabled: bool = False
    ide_port:    Optional[int] = None
    ide_auth:    str = "none"
    ide_remote:  bool = False


class ProjectUpdateRequest(BaseModel):
    account:     Optional[str]  = None
    path:        Optional[str]  = None
    active:      Optional[bool] = None
    ide_enabled: Optional[bool] = None
    ide_port:    Optional[int]  = None
    ide_auth:    Optional[str]  = None
    ide_remote:  Optional[bool] = None


class BoardCreateRequest(BaseModel):
    name:         str
    project_name: str = ""


class BoardUpdateRequest(BaseModel):
    name:         Optional[str] = None
    project_name: Optional[str] = None


class BoardCardCreateRequest(BaseModel):
    title:        str
    description:  str = ""
    project_name: str = ""
    status:       BoardCardStatus = BoardCardStatus.planned
    priority:     str = "normal"


class BoardCardUpdateRequest(BaseModel):
    title:           Optional[str] = None
    description:     Optional[str] = None
    project_name:    Optional[str] = None
    status:          Optional[BoardCardStatus] = None
    priority:        Optional[str] = None
    conversation_id: Optional[str] = None
    pipeline_id:     Optional[str] = None
    archived:        Optional[bool] = None


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


# ── 初始化 ────────────────────────────────────────────────

WORKSPACE_DIR    = Path(os.environ.get("CODERFLEET_WORKSPACE", Path.home() / ".coderfleet"))
scheduler        = Scheduler(WORKSPACE_DIR)
push_manager     = PushManager(WORKSPACE_DIR)
marketplace_mgr  = MarketplaceManager(WORKSPACE_DIR / "cache")

STATIC_DIR = Path(__file__).parent / "static"
STATIC_DIR.mkdir(exist_ok=True)

app = FastAPI(
    title       = "CoderFleet Scheduler API",
    description = "CoderFleet 任务调度服务",
    version     = "0.1.3.dev0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins     = ["*"],
    allow_credentials = True,
    allow_methods     = ["*"],
    allow_headers     = ["*"],
)

_api_key = load_api_key(WORKSPACE_DIR)
app.add_middleware(AuthMiddleware, api_key=_api_key)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.on_event("startup")
async def reconcile_tasks_on_startup():
    scheduler._push_manager = push_manager
    await scheduler.reconcile_running_tasks()
    scheduler.start_scheduling_loop()


@app.get("/", include_in_schema=False)
async def index():
    html = STATIC_DIR / "index.html"
    if not html.exists():
        return PlainTextResponse("Web UI not found.", status_code=404)
    return FileResponse(html)


@app.get("/m", include_in_schema=False)
async def mobile():
    html = STATIC_DIR / "mobile.html"
    if not html.exists():
        return PlainTextResponse("Mobile UI not found.", status_code=404)
    return FileResponse(html)


@app.get("/sw.js", include_in_schema=False)
async def service_worker():
    return FileResponse(STATIC_DIR / "sw.js", media_type="application/javascript")


def _contains_project_path(project_path: str, record_path: str) -> bool:
    base = str(project_path or "").rstrip("/")
    target = str(record_path or "").rstrip("/")
    return bool(base) and (target == base or target.startswith(base + "/"))


def _project_for_record(projects: list, project_name: str = "", project_path: str = ""):
    if project_name:
        found = next((p for p in projects if p.name == project_name), None)
        if found:
            return found
    return next((p for p in projects if _contains_project_path(p.path, project_path)), None)


def _record_project_name(projects: list, name: str = "", path: str = "") -> str:
    project = _project_for_record(projects, name, path)
    return project.name if project else name


def _search_snippet(text: str, query: str, radius: int = 48) -> str:
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


def _field_match_score(text: str, query: str, base: int) -> int:
    value = str(text or "").lower()
    q = query.lower()
    if not value or q not in value:
        return 0
    if value == q:
        return base + 80
    if value.startswith(q):
        return base + 40
    return base


def _collect_matches(fields: list[tuple[str, str, int]], query: str) -> tuple[int, list[SearchMatch]]:
    score = 0
    matches: list[SearchMatch] = []
    for field, text, weight in fields:
        field_score = _field_match_score(text, query, weight)
        if not field_score:
            continue
        score += field_score
        matches.append(SearchMatch(field=field, snippet=_search_snippet(text, query)))
    return score, matches


def _status_boost(status: str) -> int:
    return 12 if status in {"running", "pending", "scheduled"} else 0


@app.get("/api/search", response_model=SearchResponse)
async def global_search(
    q: str = Query("", description="搜索项目、对话、任务或内容"),
    scope: str = Query("all", description="all/projects/conversations/tasks/content"),
    project_name: str = Query("", description="限制到指定项目名"),
    include_archived: bool = Query(False),
    deep: bool = Query(False, description="是否搜索任务日志内容"),
    limit: int = Query(50, ge=1, le=200),
):
    query = q.strip()
    if not query:
        return SearchResponse(query=query, results=[])

    allowed_scopes = {"all", "projects", "conversations", "tasks", "content"}
    if scope not in allowed_scopes:
        raise HTTPException(status_code=400, detail=f"无效搜索范围：{scope}")

    projects = scheduler.list_projects()
    conversations = scheduler.list_conversations(include_archived=include_archived)
    tasks = scheduler.list_tasks()
    if not include_archived:
        tasks = [t for t in tasks if not getattr(t, "archived", False)]

    if project_name:
        projects = [p for p in projects if p.name == project_name]
        all_projects = scheduler.list_projects()
        conversations = [c for c in conversations if _record_project_name(all_projects, c.project_name, c.project) == project_name]
        tasks = [t for t in tasks if _record_project_name(all_projects, t.project_name, t.project) == project_name]

    project_by_name = {p.name: p for p in scheduler.list_projects()}
    results: list[SearchResult] = []

    if scope in {"all", "projects"}:
        for project in projects:
            score, matches = _collect_matches([
                ("项目名", project.name, 120),
                ("路径", project.path, 55),
                ("账号", project.account, 40),
            ], query)
            if score:
                results.append(SearchResult(
                    type="project",
                    id=project.name,
                    title=project.name,
                    subtitle=f"{project.path} · {project.account}",
                    project_name=project.name,
                    project=project.path,
                    score=score,
                    matches=matches,
                ))

    if scope in {"all", "conversations"}:
        for conv in conversations:
            project = project_by_name.get(conv.project_name) or _project_for_record(list(project_by_name.values()), "", conv.project)
            pname = project.name if project else conv.project_name
            score, matches = _collect_matches([
                ("对话名", conv.name, 120),
                ("对话 ID", conv.id, 70),
                ("项目名", pname, 60),
                ("项目路径", conv.project, 45),
                ("账号", conv.account, 35),
                ("原生会话", conv.native_session_id, 25),
            ], query)
            if score:
                results.append(SearchResult(
                    type="conversation",
                    id=conv.id,
                    title=conv.name or conv.id,
                    subtitle=" · ".join(v for v in [pname, conv.account, conv.status.value] if v),
                    project_name=pname or "",
                    project=conv.project,
                    conversation_id=conv.id,
                    status=conv.status.value,
                    updated=conv.updated or conv.created,
                    score=score,
                    matches=matches,
                ))

    if scope in {"all", "tasks"}:
        for task in tasks:
            project = project_by_name.get(task.project_name) or _project_for_record(list(project_by_name.values()), "", task.project)
            pname = project.name if project else task.project_name
            score, matches = _collect_matches([
                ("任务描述", task.prompt, 120),
                ("任务 ID", task.id, 70),
                ("对话 ID", task.conversation_id, 50),
                ("项目名", pname, 60),
                ("项目路径", task.project, 45),
                ("账号", task.account, 35),
                ("状态", task.status.value, 30),
            ], query)
            if score:
                score += _status_boost(task.status.value)
                results.append(SearchResult(
                    type="task",
                    id=task.id,
                    title=task.prompt or task.id,
                    subtitle=" · ".join(v for v in [pname, task.account, task.status.value] if v),
                    project_name=pname or "",
                    project=task.project,
                    conversation_id=task.conversation_id,
                    task_id=task.id,
                    status=task.status.value,
                    updated=task.finished or task.created,
                    score=score,
                    matches=matches,
                ))

    if deep and scope in {"all", "content"}:
        for task in tasks:
            log_path = scheduler.get_log_path(task.id)
            if not log_path.exists():
                continue
            try:
                text = log_path.read_text(encoding="utf-8", errors="ignore")[-262144:]
            except OSError:
                continue
            if query.lower() not in text.lower():
                continue
            project = project_by_name.get(task.project_name) or _project_for_record(list(project_by_name.values()), "", task.project)
            pname = project.name if project else task.project_name
            results.append(SearchResult(
                type="content",
                id=f"log-{task.id}",
                title=task.prompt or task.id,
                subtitle=" · ".join(v for v in [pname, task.status.value, f"{task.id}.log"] if v),
                project_name=pname or "",
                project=task.project,
                conversation_id=task.conversation_id,
                task_id=task.id,
                status=task.status.value,
                updated=task.finished or task.created,
                score=35 + _status_boost(task.status.value),
                matches=[SearchMatch(field="日志", snippet=_search_snippet(text, query))],
            ))

    results.sort(key=lambda r: (r.score, r.updated), reverse=True)
    return SearchResponse(query=query, results=results[:limit])


# ── Web Push ───────────────────────────────────────────────

class PushSubscribeRequest(BaseModel):
    subscription: dict


class PushUnsubscribeRequest(BaseModel):
    endpoint: str


@app.get("/api/push/vapid-public-key")
async def get_vapid_public_key():
    key = push_manager.public_key_b64
    if not key:
        raise HTTPException(503, "Web Push 不可用（pywebpush 未安装）")
    return {"publicKey": key}


@app.get("/api/push/status")
async def push_status():
    return {
        "enabled": push_manager.enabled,
        "publicKey": bool(push_manager.public_key_b64),
        "subscriptions": push_manager.subscription_count,
    }


@app.post("/api/push/subscribe", status_code=204)
async def push_subscribe(req: PushSubscribeRequest):
    push_manager.add_subscription(req.subscription)


@app.post("/api/push/unsubscribe", status_code=204)
async def push_unsubscribe(req: PushUnsubscribeRequest):
    push_manager.remove_subscription(req.endpoint)


@app.post("/api/push/test", status_code=204)
async def push_test():
    if not push_manager.enabled:
        raise HTTPException(503, "Web Push 不可用（pywebpush 未安装）")
    if push_manager.subscription_count == 0:
        raise HTTPException(404, "没有已订阅设备")
    await push_manager.send_all("CoderFleet 测试通知", "如果看到这条，手机 Web Push 链路已打通。")


# ── 健康检查 ──────────────────────────────────────────────

@app.get("/api/health")
async def health():
    return {"status": "ok", "workspace": str(WORKSPACE_DIR)}


# ── 用量统计 ──────────────────────────────────────────────

@app.get("/api/stats/usage")
async def get_usage_stats(
    project_name: str = Query("", description="按项目名过滤"),
    days: int = Query(30, ge=1, le=365, description="统计最近 N 天"),
):
    """
    聚合所有已完成任务的 token 用量和费用。
    返回总计 + 按账号 + 按项目的分组统计。
    """
    from datetime import timedelta
    cutoff = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")

    tasks = scheduler.list_tasks()

    total_input:  int   = 0
    total_output: int   = 0
    total_cost:   float = 0.0
    task_count:   int   = 0

    per_account: dict[str, dict] = {}
    per_project: dict[str, dict] = {}

    for task in tasks:
        if (task.created or "") < cutoff:
            continue
        if project_name and task.project_name != project_name:
            continue
        if task.status not in (TaskStatus.done, TaskStatus.failed):
            continue

        ti   = getattr(task, "tokens_input",  0)   or 0
        to_  = getattr(task, "tokens_output", 0)   or 0
        cost = getattr(task, "cost_usd",      0.0) or 0.0

        total_input  += ti
        total_output += to_
        total_cost   += cost
        task_count   += 1

        acc = task.account or "unknown"
        if acc not in per_account:
            per_account[acc] = {
                "tokens_input": 0, "tokens_output": 0,
                "cost_usd": 0.0, "task_count": 0,
            }
        per_account[acc]["tokens_input"]  += ti
        per_account[acc]["tokens_output"] += to_
        per_account[acc]["cost_usd"]      += cost
        per_account[acc]["task_count"]    += 1

        pname = task.project_name or ""
        if pname:
            if pname not in per_project:
                per_project[pname] = {
                    "tokens_input": 0, "tokens_output": 0,
                    "cost_usd": 0.0, "task_count": 0,
                }
            per_project[pname]["tokens_input"]  += ti
            per_project[pname]["tokens_output"] += to_
            per_project[pname]["cost_usd"]      += cost
            per_project[pname]["task_count"]    += 1

    # Round cost values
    total_cost = round(total_cost, 4)
    for v in per_account.values():
        v["cost_usd"] = round(v["cost_usd"], 4)
    for v in per_project.values():
        v["cost_usd"] = round(v["cost_usd"], 4)

    return {
        "days":          days,
        "task_count":    task_count,
        "tokens_input":  total_input,
        "tokens_output": total_output,
        "cost_usd":      total_cost,
        "per_account":   per_account,
        "per_project":   per_project,
    }


# ── 账号类型注册表 ─────────────────────────────────────────

@app.get("/api/account-types")
async def list_account_types():
    """返回所有已注册账号类型的元数据，用于前端动态渲染。"""
    return [
        {
            "id":               spec.id,
            "label":            spec.label,
            "supports_env_auth": spec.supports_env_auth,
            "env_hint":         spec.env_hint,
            "badge_bg":         spec.badge_bg,
            "badge_color":      spec.badge_color,
        }
        for spec in ACCOUNT_TYPES.values()
    ]


# ── 账号 ──────────────────────────────────────────────────

@app.get("/api/accounts", response_model=list[AccountResponse])
async def list_accounts():
    """列出所有账号，包含容器状态和忙碌状态"""
    return scheduler.list_accounts()


import re as _re

def _validate_identifier(name: str, label: str) -> None:
    if not _re.match(r'^[a-zA-Z0-9_-]+$', name):
        raise HTTPException(status_code=422, detail=f"{label}名只能包含字母、数字、横线和下划线")


def _validate_ide_port(enabled: bool, port: Optional[int]) -> None:
    if not enabled or port is None:
        return
    if port < 1024 or port > 65535:
        raise HTTPException(status_code=422, detail="IDE 端口必须在 1024-65535 之间")



@app.post("/api/accounts", status_code=201)
async def create_account(req: AccountCreateRequest):
    _validate_identifier(req.name, "账号")
    existing = {a.name for a in scheduler.get_accounts()}
    if req.name in existing:
        raise HTTPException(status_code=409, detail=f"账号 '{req.name}' 已存在")
    scheduler.save_account(req.name, req.type, req.auth, req.proxy)
    return {"ok": True, "name": req.name}


@app.put("/api/accounts/{name}")
async def update_account(name: str, req: AccountUpdateRequest):
    acc = next((a for a in scheduler.get_accounts() if a.name == name), None)
    if acc is None:
        raise HTTPException(status_code=404, detail=f"账号 '{name}' 不存在")
    scheduler.save_account(
        name,
        req.type  or acc.type,
        req.auth  or acc.auth,
        req.proxy or acc.proxy,
    )
    return {"ok": True, "name": name}


@app.delete("/api/accounts/{name}", status_code=204)
async def delete_account(name: str):
    acc = next((a for a in scheduler.get_accounts() if a.name == name), None)
    if acc is None:
        raise HTTPException(status_code=404, detail=f"账号 '{name}' 不存在")
    if name in scheduler.get_busy_accounts():
        raise HTTPException(status_code=409, detail=f"账号 '{name}' 有任务正在运行，无法删除")
    scheduler.delete_account(name)


@app.get("/api/accounts/{name}/env")
async def get_account_env(name: str):
    try:
        raw = scheduler.get_account_env(name)
        return {"vars": raw}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.put("/api/accounts/{name}/env", status_code=200)
async def set_account_env(name: str, req: EnvVarsRequest):
    try:
        scheduler.set_account_env(name, req.vars)
        return {"ok": True}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


def _account_skills_dir(account_name: str) -> Path:
    return WORKSPACE_DIR / "accounts" / account_name / "skills"


def _require_account(name: str) -> None:
    known = {a.name for a in scheduler.list_accounts()}
    if name not in known:
        raise HTTPException(status_code=404, detail=f"账号 {name!r} 不存在")


@app.get("/api/accounts/{name}/skills", response_model=list[Skill])
async def list_account_skills(name: str):
    _require_account(name)
    return Skill.load_all(_account_skills_dir(name))


@app.get("/api/accounts/{name}/skills/{slug}", response_model=Skill)
async def get_account_skill(name: str, slug: str):
    _require_account(name)
    path = _account_skills_dir(name) / slug / "SKILL.md"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"技能 {slug!r} 不存在")
    return Skill.from_file(slug, path)


@app.put("/api/accounts/{name}/skills/{slug}", response_model=Skill, status_code=200)
async def upsert_account_skill(name: str, slug: str, req: SkillUpsertRequest):
    _require_account(name)
    skill = Skill(
        slug                     = slug,
        name                     = req.name or slug,
        description              = req.description,
        user_invocable           = req.user_invocable,
        disable_model_invocation = req.disable_model_invocation,
        allowed_tools            = req.allowed_tools,
        content                  = req.content,
    )
    skill.save(_account_skills_dir(name))
    return skill


@app.delete("/api/accounts/{name}/skills/{slug}", status_code=204)
async def delete_account_skill(name: str, slug: str):
    _require_account(name)
    import shutil
    skill_dir = _account_skills_dir(name) / slug
    if not skill_dir.exists():
        raise HTTPException(status_code=404, detail=f"技能 {slug!r} 不存在")
    shutil.rmtree(skill_dir)


# ── Marketplace ────────────────────────────────────────────

@app.get("/api/marketplace/search")
async def marketplace_search(q: str = "", category: str = "", limit: int = 40):
    return await marketplace_mgr.search(q=q, category=category, limit=limit)


@app.post("/api/accounts/{name}/skills/install", response_model=Skill, status_code=201)
async def install_marketplace_skill(name: str, req: MarketplaceInstallRequest):
    _require_account(name)
    try:
        content = await marketplace_mgr.download_skill_md(req.plugin)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    skill = Skill.from_content(req.slug, content)
    skill.slug = req.slug  # always use the user-chosen slug
    skill.save(_account_skills_dir(name))
    return skill


# ── 任务提交 ──────────────────────────────────────────────

@app.post("/api/tasks", response_model=TaskResponse, status_code=201)
async def create_task(req: TaskCreateRequest):
    """
    提交任务，立即返回任务对象（异步执行）。

    匹配优先级：
      1. account 指定 → 直接用该账号
      2. project 指定 → 找挂载该路径的空闲账号
      3. type 指定    → 找对应类型的空闲账号
      4. 都不指定     → 找第一个空闲账号
    """
    try:
        task = await scheduler.submit(
            prompt         = req.prompt,
            account_name   = req.account,
            prefer_project = req.project,
            prefer_type    = req.type,
            auto           = req.auto,
            conversation_id   = req.conversation_id,
            conversation_name = req.conversation_name,
            project_name      = req.project_name,
            images            = req.images,
            execute_at        = req.execute_at,
            parent_task_id = req.parent_task_id,
            depends_on     = req.depends_on,
            pipeline_id    = req.pipeline_id,
            board_card_id  = req.board_card_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))

    return TaskResponse.from_task(task)


@app.get("/api/boards", response_model=list[BoardResponse])
async def list_boards():
    return [BoardResponse.from_board(b) for b in scheduler.list_boards()]


@app.post("/api/boards", response_model=BoardResponse, status_code=201)
async def create_board(req: BoardCreateRequest):
    name = req.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="看板名称不能为空")
    if req.project_name and scheduler.find_project_by_name(req.project_name) is None:
        raise HTTPException(status_code=404, detail=f"项目 '{req.project_name}' 不存在")
    board = scheduler.create_board(name, req.project_name)
    return BoardResponse.from_board(board)


@app.patch("/api/boards/{board_id}", response_model=BoardResponse)
async def update_board(board_id: str, req: BoardUpdateRequest):
    if req.project_name and scheduler.find_project_by_name(req.project_name) is None:
        raise HTTPException(status_code=404, detail=f"项目 '{req.project_name}' 不存在")
    try:
        board = scheduler.update_board(board_id, req.name, req.project_name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return BoardResponse.from_board(board)


@app.delete("/api/boards/{board_id}", status_code=204)
async def delete_board(board_id: str):
    try:
        scheduler.delete_board(board_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/api/boards/{board_id}/cards", response_model=list[BoardCardResponse])
async def list_board_cards(
    board_id: str,
    include_archived: bool = Query(False, description="是否包含已归档专题"),
):
    if scheduler.get_board(board_id) is None:
        raise HTTPException(status_code=404, detail=f"看板 '{board_id}' 不存在")
    return [
        BoardCardResponse.from_card(c)
        for c in scheduler.list_board_cards(board_id=board_id, include_archived=include_archived)
    ]


@app.post("/api/boards/{board_id}/cards", response_model=BoardCardResponse, status_code=201)
async def create_board_card(board_id: str, req: BoardCardCreateRequest):
    if req.project_name and scheduler.find_project_by_name(req.project_name) is None:
        raise HTTPException(status_code=404, detail=f"项目 '{req.project_name}' 不存在")
    try:
        card = scheduler.create_board_card(
            board_id=board_id,
            title=req.title,
            description=req.description,
            project_name=req.project_name,
            status=req.status,
            priority=req.priority,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return BoardCardResponse.from_card(card)


@app.patch("/api/board-cards/{card_id}", response_model=BoardCardResponse)
async def update_board_card(card_id: str, req: BoardCardUpdateRequest):
    if req.project_name and scheduler.find_project_by_name(req.project_name) is None:
        raise HTTPException(status_code=404, detail=f"项目 '{req.project_name}' 不存在")
    try:
        card = scheduler.update_board_card(
            card_id=card_id,
            title=req.title,
            description=req.description,
            project_name=req.project_name,
            status=req.status,
            priority=req.priority,
            conversation_id=req.conversation_id,
            pipeline_id=req.pipeline_id,
            archived=req.archived,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return BoardCardResponse.from_card(card)


@app.delete("/api/board-cards/{card_id}", status_code=204)
async def delete_board_card(card_id: str):
    try:
        scheduler.delete_board_card(card_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/api/projects", response_model=list[ProjectResponse])
async def list_projects():
    return [
        ProjectResponse.from_project(p)
        for p in scheduler.list_projects()
    ]


@app.post("/api/projects", status_code=201)
async def create_project(req: ProjectCreateRequest):
    _validate_identifier(req.name, "项目")
    _validate_ide_port(req.ide_enabled, req.ide_port)
    if any(p.name == req.name for p in scheduler.get_projects()):
        raise HTTPException(status_code=409, detail=f"项目 '{req.name}' 已存在")
    if not any(a.name == req.account for a in scheduler.get_accounts()):
        raise HTTPException(status_code=404, detail=f"账号 '{req.account}' 不存在")
    try:
        project = scheduler.save_project(req.name, req.account, req.path, req.active, req.ide_enabled, req.ide_port, req.ide_auth, req.ide_remote)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return ProjectResponse.from_project(project)


@app.put("/api/projects/{name}")
async def update_project(name: str, req: ProjectUpdateRequest):
    existing = next((p for p in scheduler.get_projects() if p.name == name), None)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"项目 '{name}' 不存在")
    new_account = req.account or existing.account
    new_path    = req.path    or existing.path
    new_active  = existing.active if req.active is None else req.active
    new_ide_enabled = existing.ide_enabled if req.ide_enabled is None else req.ide_enabled
    new_ide_port = existing.ide_port if req.ide_port is None else req.ide_port
    new_ide_auth   = existing.ide_auth   if req.ide_auth   is None else req.ide_auth
    new_ide_remote = existing.ide_remote if req.ide_remote is None else req.ide_remote
    if not new_ide_enabled:
        new_ide_port = None
    _validate_ide_port(new_ide_enabled, new_ide_port)
    if req.account and not any(a.name == new_account for a in scheduler.get_accounts()):
        raise HTTPException(status_code=404, detail=f"账号 '{new_account}' 不存在")
    try:
        project = scheduler.save_project(name, new_account, new_path, new_active, new_ide_enabled, new_ide_port, new_ide_auth, new_ide_remote)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return ProjectResponse.from_project(project)


@app.delete("/api/projects/{name}", status_code=204)
async def delete_project(name: str):
    if not any(p.name == name for p in scheduler.get_projects()):
        raise HTTPException(status_code=404, detail=f"项目 '{name}' 不存在")
    scheduler.delete_project(name)


@app.get("/api/projects/{name}/env")
async def get_project_env(name: str):
    try:
        raw = scheduler.get_project_env(name)
        return {"vars": raw}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.put("/api/projects/{name}/env", status_code=200)
async def set_project_env(name: str, req: EnvVarsRequest):
    try:
        scheduler.set_project_env(name, req.vars)
        return {"ok": True}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ── 系统运维 ──────────────────────────────────────────────

@app.post("/api/system/apply")
async def system_apply():
    """重新生成 docker-compose.yml 并重启所有容器，SSE 流式输出进度。"""
    from coderfleet.compose import write_compose
    from coderfleet.docker_ops import _dc

    async def _stream() -> AsyncIterator[str]:
        try:
            yield ">>> 生成 docker-compose.yml...\n"
            await asyncio.get_event_loop().run_in_executor(None, write_compose, WORKSPACE_DIR)
            yield "✓ docker-compose.yml 已生成\n\n"

            yield ">>> 停止旧容器 (down --remove-orphans)...\n"
            dc = _dc(WORKSPACE_DIR)
            proc = await asyncio.create_subprocess_exec(
                *dc, "down", "--remove-orphans",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            async for line in proc.stdout:
                yield line.decode("utf-8", errors="replace")
            await proc.wait()

            yield "\n>>> 启动容器 (up -d --force-recreate)...\n"
            proc = await asyncio.create_subprocess_exec(
                *dc, "up", "-d", "--force-recreate",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            async for line in proc.stdout:
                yield line.decode("utf-8", errors="replace")
            rc = await proc.wait()

            if rc != 0:
                yield f"\n✗ 容器启动失败（exit={rc}）\n"
            else:
                yield "\n✓ 完成！所有容器已重启\n"
        except Exception as e:
            yield f"\n✗ 操作失败：{e}\n"

    return StreamingResponse(_stream(), media_type="text/plain; charset=utf-8")


# ── 文件上传 ──────────────────────────────────────────────


@app.post("/api/uploads")
async def upload_file(
    file: UploadFile = File(...),
    project_name: str = Query(..., description="项目名称"),
):
    """上传文件到项目工作目录，返回容器内可访问的路径。"""
    project = scheduler.find_project_by_name(project_name)
    if project is None:
        raise HTTPException(status_code=404, detail=f"项目 '{project_name}' 不存在")

    original_name = file.filename or "upload"
    ext = Path(original_name).suffix.lower()

    upload_dir = Path(project.path) / ".coderfleet-uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)

    file_id = uuid.uuid4().hex[:16]
    filename = f"{file_id}{ext}"
    save_path = upload_dir / filename

    content = await file.read()
    save_path.write_bytes(content)

    return {
        "container_path": f"/workspace/.coderfleet-uploads/{filename}",
        "preview_url": f"/api/uploads/{project_name}/{filename}",
        "filename": original_name,
    }


@app.get("/api/uploads/{project_name}/{filename}")
async def serve_upload(project_name: str, filename: str):
    """预览已上传的图片。"""
    project = scheduler.find_project_by_name(project_name)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")

    safe_name = Path(filename).name
    file_path = Path(project.path) / ".coderfleet-uploads" / safe_name
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="文件不存在")

    return FileResponse(file_path)


def _serve_project_file(project_path: str, rel_path: str) -> FileResponse:
    """公共实现：安全校验并返回项目目录下的文件。"""
    base_dir = Path(project_path).resolve()
    target = (base_dir / rel_path).resolve()
    try:
        target.relative_to(base_dir)
    except ValueError:
        raise HTTPException(status_code=403, detail="路径不合法")
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="文件不存在或不是普通文件")
    return FileResponse(
        target,
        filename=target.name,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{target.name}"'},
    )


@app.get("/api/projects/{project_name}/download")
async def download_project_file(
    project_name: str,
    path: str = Query(..., description="相对于项目目录的文件路径"),
):
    """从项目目录下载任意文件（CF_SEND 标记主要入口）。"""
    project = scheduler.find_project_by_name(project_name)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    return _serve_project_file(project.path, path)


@app.get("/api/tasks/{task_id}/download")
async def download_task_file(
    task_id: str,
    path: str = Query(..., description="相对于项目目录的文件路径"),
):
    """从任务所在项目目录下载文件（兼容旧标记）。"""
    task = scheduler.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    project = scheduler.find_project_by_name(task.project_name) if task.project_name else None
    proj_path = project.path if project else task.project
    return _serve_project_file(proj_path, path)


# ── 项目终端 ──────────────────────────────────────────────

def _is_allowed_terminal_origin(origin: str | None, host: str | None) -> bool:
    if not origin:
        return True
    parsed = urlparse(origin)
    origin_host = parsed.hostname or ""
    request_host = (host or "").split(":", 1)[0]
    return origin_host in {"localhost", "127.0.0.1", "::1", request_host}


@app.websocket("/api/projects/{project_name}/terminal")
async def project_terminal(websocket: WebSocket, project_name: str):
    if not _is_allowed_terminal_origin(
        websocket.headers.get("origin"),
        websocket.headers.get("host"),
    ):
        await websocket.close(code=1008)
        return

    await websocket.accept()
    session: TerminalSession | None = None

    try:
        try:
            target = resolve_terminal_target(scheduler, project_name)
        except (ValueError, RuntimeError) as e:
            await websocket.send_json({
                "type": "status",
                "state": "error",
                "message": str(e),
            })
            await websocket.close(code=1008)
            return

        session = TerminalSession(target.command, project_name=target.project.name)
        session.start()
        await websocket.send_json({
            "type": "status",
            "state": "connected",
            "message": f"已连接 {target.container_name}:{target.container_workdir}",
        })

        async def pump_output() -> None:
            assert session is not None
            while True:
                data = await session.read()
                if data:
                    await websocket.send_json({
                        "type": "output",
                        "data": data.decode("utf-8", errors="replace"),
                    })
                else:
                    await asyncio.sleep(0.02)

        async def pump_input() -> None:
            assert session is not None
            while True:
                raw = await websocket.receive_text()
                try:
                    message = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                message_type = message.get("type")
                if message_type == "input":
                    session.write(str(message.get("data", "")))
                elif message_type == "resize":
                    try:
                        cols = int(message.get("cols", 0))
                        rows = int(message.get("rows", 0))
                    except (TypeError, ValueError):
                        continue
                    session.resize(cols=cols, rows=rows)

        output_task = asyncio.create_task(pump_output())
        input_task = asyncio.create_task(pump_input())
        done, pending = await asyncio.wait(
            {output_task, input_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        for task in done:
            task.result()
    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await websocket.send_json({
                "type": "status",
                "state": "error",
                "message": str(e),
            })
        except Exception:
            pass
    finally:
        if session is not None:
            session.close()
        try:
            await websocket.send_json({
                "type": "status",
                "state": "closed",
                "message": "终端连接已关闭",
            })
        except Exception:
            pass


# ── 任务链 ────────────────────────────────────────────────

@app.get("/api/conversations", response_model=list[ConversationResponse])
async def list_conversations(include_archived: bool = Query(False)):
    return [
        ConversationResponse.from_conversation(c)
        for c in scheduler.list_conversations(include_archived=include_archived)
    ]


@app.post("/api/conversations", response_model=ConversationResponse, status_code=201)
async def create_conversation(req: ConversationCreateRequest):
    """
    从已执行过的任务（需有 native_session_id）创建任务链。
    后续可通过 conversation_id 续接该会话的上下文。
    """
    try:
        conversation = scheduler.create_conversation_from_task(
            name    = req.name,
            task_id = req.task_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return ConversationResponse.from_conversation(conversation)


@app.get("/api/conversations/{conversation_id}", response_model=ConversationResponse)
async def get_conversation(conversation_id: str):
    conversation = scheduler.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail=f"任务链 '{conversation_id}' 不存在")
    return ConversationResponse.from_conversation(conversation)


class ConversationStatusUpdate(BaseModel):
    status: Optional[ConversationStatus] = None
    name: Optional[str] = None


@app.patch("/api/conversations/{conversation_id}", response_model=ConversationResponse)
async def update_conversation(conversation_id: str, body: ConversationStatusUpdate):
    try:
        if body.name is not None:
            conv = scheduler.rename_conversation(conversation_id, body.name)
        elif body.status is not None:
            conv = scheduler.archive_conversation(conversation_id, body.status)
        else:
            conv = scheduler.get_conversation(conversation_id)
            if conv is None:
                raise ValueError(f"任务链 '{conversation_id}' 不存在")
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return ConversationResponse.from_conversation(conv)


@app.delete("/api/conversations/{conversation_id}", status_code=204)
async def delete_conversation(conversation_id: str):
    try:
        scheduler.delete_conversation(conversation_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ── 任务列表 ──────────────────────────────────────────────

@app.get("/api/tasks", response_model=list[TaskResponse])
async def list_tasks(
    status:  Optional[str] = Query(None, description="按状态过滤：running/done/failed/killed"),
    account: Optional[str] = Query(None, description="按账号名过滤"),
    limit:   int           = Query(50,   description="返回条数上限"),
    include_archived: bool = Query(False, description="是否包含已归档的任务"),
):
    tasks = scheduler.list_tasks()

    if not include_archived:
        tasks = [t for t in tasks if not getattr(t, "archived", False)]

    if status:
        try:
            s = TaskStatus(status)
            tasks = [t for t in tasks if t.status == s]
        except ValueError:
            raise HTTPException(status_code=400, detail=f"无效状态值：{status}")

    if account:
        tasks = [t for t in tasks if t.account == account]

    return [TaskResponse.from_task(t) for t in tasks[:limit]]


# ── 任务详情 ──────────────────────────────────────────────

@app.get("/api/tasks/{task_id}", response_model=TaskResponse)
async def get_task(task_id: str):
    task = scheduler.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"任务 '{task_id}' 不存在")
    return TaskResponse.from_task(task)


# ── 终止任务 ──────────────────────────────────────────────

@app.delete("/api/tasks/{task_id}", response_model=TaskResponse)
async def kill_task(task_id: str):
    try:
        task = await scheduler.kill_task(task_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return TaskResponse.from_task(task)


class TaskUpdate(BaseModel):
    archived: bool


@app.patch("/api/tasks/{task_id}", response_model=TaskResponse)
async def update_task(task_id: str, body: TaskUpdate):
    try:
        task = scheduler.archive_task(task_id, body.archived)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return TaskResponse.from_task(task)


@app.post("/api/tasks/{task_id}/retry", response_model=TaskResponse)
async def retry_task(task_id: str):
    """重试一个 failed 或 killed 状态的任务，克隆原任务重新提交。"""
    original = scheduler.get_task(task_id)
    if original is None:
        raise HTTPException(status_code=404, detail=f"任务 '{task_id}' 不存在")
    if original.status not in (TaskStatus.failed, TaskStatus.killed):
        raise HTTPException(
            status_code=409,
            detail=f"只能重试 failed/killed 状态的任务，当前状态：{original.status.value}",
        )
    try:
        new_task = await scheduler.submit(
            prompt         = original.prompt,
            project_name   = original.project_name or None,
            auto           = getattr(original, "auto", False),
            pipeline_id    = original.pipeline_id or None,
            parent_task_id = original.parent_task_id or None,
            depends_on     = [],
            board_card_id  = original.board_card_id or None,
        )
        if original.pipeline_id:
            scheduler.update_pipeline_node_task(original.pipeline_id, task_id, new_task.id)
        return TaskResponse.from_task(new_task)
    except (ValueError, RuntimeError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/api/tasks/{task_id}/record", status_code=204)
async def delete_task_record(task_id: str):
    try:
        scheduler.delete_task(task_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))


# ── 完整日志（文本）──────────────────────────────────────

@app.get("/api/tasks/{task_id}/logs", response_class=PlainTextResponse)
async def get_logs(task_id: str):
    log_path = scheduler.get_log_path(task_id)
    if not log_path.exists():
        raise HTTPException(status_code=404, detail=f"日志文件不存在：{task_id}")
    return log_path.read_text(encoding="utf-8")


# ── SSE 实时日志流 ────────────────────────────────────────

@app.get("/api/tasks/{task_id}/logs/stream")
async def stream_logs(
    task_id:    str,
    tail:       int = Query(50, ge=0, description="从末尾多少行开始推送；0 = 不推送已有内容"),
    skip_bytes: int = Query(0,  ge=0, description="客户端已获取的字节偏移量；>0 时忽略 tail，从此处开始推送"),
):
    """
    Server-Sent Events 实时日志流。

    协议：
      data: <日志行内容>\n\n
      data: [DONE]\n\n   ← 任务结束时发送，客户端可关闭连接

    防重复策略：
      - 客户端若已通过 GET /logs 获取了完整日志，应传 skip_bytes=<已获取字节数>，
        服务端从该偏移量开始推送，彻底避免重复渲染。
      - 退化路径：skip_bytes=0 且 tail=0 时不推送任何已有内容（注意 Python 的
        -0 == 0 陷阱，原实现有 bug，此处已修复）。
    """
    log_path = scheduler.get_log_path(task_id)

    async def _read_from(offset: int) -> tuple[bytes, int]:
        """从 offset 字节处读取到文件末尾，返回 (内容, 新的文件大小)。"""
        if not log_path.exists():
            return b"", 0
        cur_size = log_path.stat().st_size
        start = min(offset, cur_size)
        if cur_size <= start:
            return b"", cur_size
        async with aiofiles.open(log_path, "rb") as f:
            await f.seek(start)
            data = await f.read()
        return data, cur_size

    async def generate() -> AsyncIterator[str]:
        last_size: int = 0

        if skip_bytes > 0:
            # ── 精确模式：客户端已持有前 skip_bytes 字节，从此处开始推送剩余内容 ──
            new_bytes, last_size = await _read_from(skip_bytes)
            if new_bytes:
                for line in new_bytes.decode("utf-8", errors="replace").splitlines(keepends=True):
                    yield f"data: {line.rstrip()}\n\n"

        elif tail > 0:
            # ── 末尾行模式：推送末尾 tail 行历史 ──
            # 注意：不能用 existing_lines[-tail:] 而后把 tail 设为 0，
            # 因为 Python 中 -0 == 0，-0 切片会返回全部内容。
            if log_path.exists():
                async with aiofiles.open(log_path, "rb") as f:
                    raw = await f.read()
                lines = raw.decode("utf-8", errors="replace").splitlines(keepends=True)
                for line in lines[-tail:]:
                    yield f"data: {line.rstrip()}\n\n"
                last_size = log_path.stat().st_size

        else:
            # ── tail=0, skip_bytes=0：不推送任何已有内容，直接从文件末尾开始 tail ──
            last_size = log_path.stat().st_size if log_path.exists() else 0

        # 若任务已不存在（被删除），直接结束
        task = scheduler.get_task(task_id)
        if task is None:
            yield "data: [DONE]\n\n"
            return

        # ── 持续 tail 循环：每 0.3 秒检测文件增量并推送 ──
        while True:
            await asyncio.sleep(0.3)

            task = scheduler.get_task(task_id)
            if task is None:
                yield "data: [DONE]\n\n"
                return

            # 如果还在排队或定时中，持续等待其被调度拉起
            if task.status in (TaskStatus.pending, TaskStatus.scheduled):
                continue

            # 任务是否已结束
            is_done = task.status not in (TaskStatus.running, TaskStatus.pending, TaskStatus.scheduled)

            if log_path.exists():
                cur_size = log_path.stat().st_size
                if cur_size > last_size:
                    async with aiofiles.open(log_path, "rb") as f:
                        await f.seek(last_size)
                        new_bytes = await f.read()
                    last_size = cur_size
                    for line in new_bytes.decode("utf-8", errors="replace").splitlines(keepends=True):
                        yield f"data: {line.rstrip()}\n\n"

            if is_done:
                yield "data: [DONE]\n\n"
                return

    # 检查日志文件（running 状态的任务日志可能正在生成）
    task = scheduler.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"任务 '{task_id}' 不存在")

    return StreamingResponse(
        generate(),
        media_type  = "text/event-stream",
        headers     = {
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",    # 禁止 nginx 缓冲
        },
    )


# ── 清理旧任务 ────────────────────────────────────────────

@app.post("/api/tasks/clean")
async def clean_tasks(keep: int = Query(30, description="保留最近 N 条记录")):
    cleaned = scheduler.clean_tasks(keep=keep)
    return {"cleaned": cleaned, "kept": keep}


# ── 派生子任务 ────────────────────────────────────────────

class SubtaskCreateRequest(BaseModel):
    prompt:          str
    project_name:    str
    auto:            bool = True
    wait_for_parent: bool = True
    pipeline_id:     Optional[str] = None
    images:          list[str] = []


@app.post("/api/tasks/{task_id}/subtasks", response_model=TaskResponse, status_code=201)
async def create_subtask(task_id: str, req: SubtaskCreateRequest):
    try:
        task = await scheduler.spawn_subtask(
            parent_task_id  = task_id,
            prompt          = req.prompt,
            project_name    = req.project_name,
            auto            = req.auto,
            wait_for_parent = req.wait_for_parent,
            pipeline_id     = req.pipeline_id,
            images          = req.images,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return TaskResponse.from_task(task)


# ── 逻辑项目（按路径分组） ────────────────────────────────

@app.get("/api/projects/logical", response_model=list[LogicalProject])
async def list_logical_projects():
    return scheduler.get_logical_projects()


# ── Pipeline CRUD ─────────────────────────────────────────

class PipelineCreateRequest(BaseModel):
    name:         str
    project_name: str = ""
    task_ids:     list[str] = []


class PipelineAddTaskRequest(BaseModel):
    task_id: str


@app.get("/api/pipelines", response_model=list[PipelineResponse])
async def list_pipelines():
    pipelines = scheduler.list_pipelines()
    all_tasks = {t.id: t for t in scheduler.list_tasks()}
    return [
        PipelineResponse.from_pipeline(p, [all_tasks[tid] for tid in p.task_ids if tid in all_tasks])
        for p in pipelines
    ]


@app.post("/api/pipelines", response_model=PipelineResponse, status_code=201)
async def create_pipeline(req: PipelineCreateRequest):
    pipeline = scheduler.create_pipeline(
        name         = req.name,
        task_ids     = req.task_ids,
        project_name = req.project_name,
    )
    return PipelineResponse.from_pipeline(pipeline)


@app.get("/api/pipelines/{pipeline_id}", response_model=PipelineResponse)
async def get_pipeline(pipeline_id: str):
    pipeline = scheduler.get_pipeline(pipeline_id)
    if pipeline is None:
        raise HTTPException(status_code=404, detail=f"工作流 '{pipeline_id}' 不存在")
    all_tasks = {t.id: t for t in scheduler.list_tasks()}
    tasks = [all_tasks[tid] for tid in pipeline.task_ids if tid in all_tasks]
    return PipelineResponse.from_pipeline(pipeline, tasks)


@app.post("/api/pipelines/{pipeline_id}/tasks", response_model=PipelineResponse)
async def add_task_to_pipeline(pipeline_id: str, req: PipelineAddTaskRequest):
    try:
        pipeline = scheduler.add_task_to_pipeline(pipeline_id, req.task_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    all_tasks = {t.id: t for t in scheduler.list_tasks()}
    tasks = [all_tasks[tid] for tid in pipeline.task_ids if tid in all_tasks]
    return PipelineResponse.from_pipeline(pipeline, tasks)


@app.delete("/api/pipelines/{pipeline_id}", status_code=204)
async def delete_pipeline(pipeline_id: str):
    try:
        scheduler.delete_pipeline(pipeline_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ══════════════════════════════════════════════════════════════
#  定时计划 CRUD
# ══════════════════════════════════════════════════════════════

@app.get("/api/schedules", response_model=list[ScheduleResponse])
async def list_schedules():
    return [ScheduleResponse.from_schedule(s) for s in scheduler.list_schedules()]


@app.post("/api/schedules", response_model=ScheduleResponse, status_code=201)
async def create_schedule(req: ScheduleCreateRequest):
    sched = Schedule(
        id            = scheduler.new_schedule_id(),
        name          = req.name,
        prompt        = req.prompt,
        project_name  = req.project_name,
        schedule_type = req.schedule_type,
        time_of_day   = req.time_of_day,
        days_of_week  = req.days_of_week,
        minute_of_hour= req.minute_of_hour,
        enabled       = req.enabled,
        auto          = req.auto,
        account       = req.account,
    )
    sched = scheduler.create_schedule(sched)
    return ScheduleResponse.from_schedule(sched)


@app.get("/api/schedules/{sched_id}", response_model=ScheduleResponse)
async def get_schedule(sched_id: str):
    sched = scheduler.get_schedule(sched_id)
    if sched is None:
        raise HTTPException(status_code=404, detail=f"定时计划 '{sched_id}' 不存在")
    return ScheduleResponse.from_schedule(sched)


@app.put("/api/schedules/{sched_id}", response_model=ScheduleResponse)
async def update_schedule(sched_id: str, req: ScheduleUpdateRequest):
    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    sched = scheduler.update_schedule(sched_id, updates)
    if sched is None:
        raise HTTPException(status_code=404, detail=f"定时计划 '{sched_id}' 不存在")
    return ScheduleResponse.from_schedule(sched)


@app.delete("/api/schedules/{sched_id}", status_code=204)
async def delete_schedule(sched_id: str):
    if not scheduler.delete_schedule(sched_id):
        raise HTTPException(status_code=404, detail=f"定时计划 '{sched_id}' 不存在")


@app.post("/api/schedules/{sched_id}/toggle", response_model=ScheduleResponse)
async def toggle_schedule(sched_id: str):
    sched = scheduler.get_schedule(sched_id)
    if sched is None:
        raise HTTPException(status_code=404, detail=f"定时计划 '{sched_id}' 不存在")
    sched = scheduler.update_schedule(sched_id, {"enabled": not sched.enabled})
    return ScheduleResponse.from_schedule(sched)


@app.post("/api/schedules/{sched_id}/run-now", response_model=TaskResponse, status_code=201)
async def run_schedule_now(sched_id: str):
    sched = scheduler.get_schedule(sched_id)
    if sched is None:
        raise HTTPException(status_code=404, detail=f"定时计划 '{sched_id}' 不存在")
    task = await scheduler.submit(
        prompt       = sched.prompt,
        project_name = sched.project_name,
        auto         = sched.auto,
        account_name = sched.account,
    )
    sched.last_run_at = datetime.now().isoformat(timespec="seconds")
    sched.last_task_id = task.id
    sched.updated = datetime.now().isoformat(timespec="seconds")
    sched.save(scheduler.schedules_dir)
    return TaskResponse.from_task(task)


# ══════════════════════════════════════════════════════════════
#  工作流模板
# ══════════════════════════════════════════════════════════════

@app.get("/api/workflow-templates", response_model=list[WorkflowTemplateResponse])
async def list_workflow_templates():
    return [WorkflowTemplateResponse.from_template(t) for t in scheduler.list_templates()]


@app.post("/api/workflow-templates", response_model=WorkflowTemplateResponse, status_code=201)
async def create_workflow_template(req: TemplateCreateRequest):
    tpl = scheduler.create_template(req.name, req.description, req.nodes)
    return WorkflowTemplateResponse.from_template(tpl)


@app.get("/api/workflow-templates/{template_id}", response_model=WorkflowTemplateResponse)
async def get_workflow_template(template_id: str):
    tpl = scheduler.get_template(template_id)
    if tpl is None:
        raise HTTPException(status_code=404, detail=f"模板 '{template_id}' 不存在")
    return WorkflowTemplateResponse.from_template(tpl)


@app.put("/api/workflow-templates/{template_id}", response_model=WorkflowTemplateResponse)
async def update_workflow_template(template_id: str, req: TemplateCreateRequest):
    try:
        tpl = scheduler.update_template(template_id, req.name, req.description, req.nodes)
        return WorkflowTemplateResponse.from_template(tpl)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.delete("/api/workflow-templates/{template_id}", status_code=204)
async def delete_workflow_template(template_id: str):
    try:
        scheduler.delete_template(template_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/api/workflow-templates/{template_id}/run", response_model=PipelineResponse, status_code=201)
async def run_workflow_template(template_id: str, req: TemplateRunRequest):
    try:
        pipeline = await scheduler.run_template(
            template_id     = template_id,
            input_str       = req.input,
            project_map     = req.project_map,
            default_project = req.default_project,
        )
        all_tasks = {t.id: t for t in scheduler.list_tasks()}
        tasks = [all_tasks[tid] for tid in pipeline.task_ids if tid in all_tasks]
        return PipelineResponse.from_pipeline(pipeline, tasks)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── 启动入口 ──────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "coderfleet.server.main:app",
        host    = "0.0.0.0",
        port    = int(os.environ.get("CODERFLEET_PORT", 8765)),
        reload  = False,
        workers = 1,        # 单进程，scheduler 状态在内存里
    )
