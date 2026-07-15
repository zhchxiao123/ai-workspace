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
  POST /api/image/build           触发共享镜像构建
  DELETE /api/image/build/{id}    停止共享镜像构建
  GET  /api/builds                列出镜像构建历史（含共享镜像与项目专属镜像）
  GET  /api/builds/{id}           查看某次构建详情
  GET  /api/builds/{id}/logs      获取某次构建的完整日志（文本）
  GET  /api/builds/{id}/logs/stream  SSE 实时构建日志流（可对已结束的构建重放）
  GET  /api/health                健康检查
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import AsyncIterator, Optional
from urllib.parse import urlparse

import uuid

import aiofiles
from fastapi import FastAPI, File, HTTPException, Query, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from coderfleet.server.models import (
    Account,
    AccountAuth,
    AccountProxy,
    AccountResponse,
    AccountType,
    AccountUsage,
    BoardCardResponse,
    BoardCardStatus,
    BoardResponse,
    Conversation,
    ConversationMode,
    ConversationResponse,
    ConversationStatus,
    ImageBuild,
    ImageBuildStatus,
    LogicalProject,
    MarketplaceInstallRequest,
    Pipeline,
    PipelineResponse,
    ProjectResponse,
    Schedule,
    ScheduleCreateRequest,
    ScheduleResponse,
    ScheduleRunResponse,
    ScheduleType,
    ScheduleUpdateRequest,
    Skill,
    SkillUpsertRequest,
    Task,
    TaskCreateRequest,
    TaskHeartbeat,
    TaskResponse,
    TaskStatus,
    TemplateCreateRequest,
    TemplateRunRequest,
    TerminalConversationCreateRequest,
    WorkflowRun,
    WorkflowRunResponse,
    WorkflowTemplateResponse,
)
from coderfleet.account_type_registry import duplicate_account_types
from coderfleet.server.auth import AuthMiddleware, load_api_key
from coderfleet.server.image_builds import ImageBuildRegistry
from coderfleet.server.image_build_runner import run_image_build
from coderfleet.server.marketplace import MarketplaceManager
from coderfleet.server.scheduler import MAX_PENDING_PER_CONV, Scheduler
from coderfleet.server.system_llm import SystemLLM, SystemLLMError
from coderfleet.server.translate import translate_text
from coderfleet.server.translation_cache import TranslationCache
from coderfleet.server.settings_schema import SETTINGS_GROUPS, field_for, mask_secret
from coderfleet.config import load_config as _load_config, set_config as _set_config
from coderfleet.server.search import SCOPES, SearchResponse, rank_paths, search_records
from coderfleet.server.terminal import (
    TerminalSession,
    TmuxTerminalSession,
    is_tmux_session_alive,
    resolve_terminal_target,
    setup_tmux_session,
)
from coderfleet.server.push_manager import PushManager, task_push_notifier
from coderfleet.server.telegram_bridge import TelegramBridge, TelegramError
from coderfleet.server.digest import (
    build_generate_prompt,
    compute_daily_stats,
    list_active_dates,
    load_digest,
)
from coderfleet.server.models import DailyDigest, DigestStatus
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


class TranslateRequest(BaseModel):
    text:   str
    target: str = "简体中文"


class TranslateResponse(BaseModel):
    translated: str
    cached:     bool = False


class ConfigUpdateRequest(BaseModel):
    updates: dict[str, str]


class ProjectCreateRequest(BaseModel):
    name:        str
    account:     str
    path:        str
    active:      bool = True
    ide_enabled: bool = False
    ide_port:    Optional[int] = None
    ide_auth:    str = "none"
    ide_remote:  bool = False
    image:       str = ""
    docker_socket: str = ""
    secondary_accounts: list[str] = []


class ProjectUpdateRequest(BaseModel):
    account:     Optional[str]  = None
    path:        Optional[str]  = None
    active:      Optional[bool] = None
    ide_enabled: Optional[bool] = None
    ide_port:    Optional[int]  = None
    ide_auth:    Optional[str]  = None
    ide_remote:  Optional[bool] = None
    image:       Optional[str]  = None
    docker_socket: Optional[str] = None
    secondary_accounts: Optional[list[str]] = None


class BoardCreateRequest(BaseModel):
    name:         str


class BoardUpdateRequest(BaseModel):
    name:         Optional[str] = None


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


# ── 初始化 ────────────────────────────────────────────────

# 应用日志：uvicorn 只配置自己的 logger，coderfleet.* 默认只有 WARNING
# 能经 lastResort 冒出来；这里补一个 INFO 级 handler（带时间戳），
# 否则通知/轮询的全链路日志在 server.log 里不可见。
_app_logger = logging.getLogger("coderfleet")
if not _app_logger.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    _app_logger.addHandler(_h)
    _app_logger.setLevel(logging.INFO)
    _app_logger.propagate = False

WORKSPACE_DIR    = Path(os.environ.get("CODERFLEET_WORKSPACE", Path.home() / ".coderfleet"))
scheduler        = Scheduler(WORKSPACE_DIR)
push_manager     = PushManager(WORKSPACE_DIR)
telegram_bridge  = TelegramBridge(WORKSPACE_DIR)
marketplace_mgr  = MarketplaceManager(WORKSPACE_DIR / "cache")
translation_cache = TranslationCache(WORKSPACE_DIR / "translations")
DIGEST_DIR       = WORKSPACE_DIR / "digests"
image_builds     = ImageBuildRegistry()

STATIC_DIR = Path(__file__).parent / "static"
STATIC_DIR.mkdir(exist_ok=True)

def _build_version() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL,
            cwd=Path(__file__).parent,
        ).decode().strip()
    except Exception:
        return "dev"

_BUILD_VERSION = _build_version()

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
    scheduler._push_manager = push_manager  # 工作流审批等非任务消息
    scheduler.register_notifier(task_push_notifier(push_manager))
    scheduler.register_notifier(telegram_bridge.notify_task)
    telegram_bridge.scheduler = scheduler
    telegram_bridge.start_polling()
    await scheduler.reconcile_running_tasks()
    scheduler.start_scheduling_loop()
    scheduler.start_usage_polling_loop()


@app.get("/", include_in_schema=False)
async def index():
    html = STATIC_DIR / "index.html"
    if not html.exists():
        return PlainTextResponse("Web UI not found.", status_code=404)
    content = html.read_text(encoding="utf-8").replace("__BUILD__", _BUILD_VERSION)
    return HTMLResponse(content=content)


@app.get("/m", include_in_schema=False)
async def mobile():
    html = STATIC_DIR / "mobile.html"
    if not html.exists():
        return PlainTextResponse("Mobile UI not found.", status_code=404)
    return FileResponse(html)


@app.get("/sw.js", include_in_schema=False)
async def service_worker():
    return FileResponse(STATIC_DIR / "sw.js", media_type="application/javascript")


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

    if scope not in SCOPES:
        raise HTTPException(status_code=400, detail=f"无效搜索范围：{scope}")

    tasks = scheduler.list_tasks()
    if not include_archived:
        tasks = [t for t in tasks if not getattr(t, "archived", False)]

    def read_log(task_id: str) -> Optional[str]:
        path = scheduler.get_log_path(task_id)
        if not path.exists():
            return None
        try:
            return path.read_text(encoding="utf-8", errors="ignore")[-262144:]
        except OSError:
            return None

    results = search_records(
        query, scope,
        projects=scheduler.list_projects(),
        conversations=scheduler.list_conversations(include_archived=include_archived),
        tasks=tasks,
        project_name=project_name,
        deep=deep,
        read_log=read_log,
        limit=limit,
    )
    return SearchResponse(query=query, results=results)


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


@app.get("/api/telegram/status")
async def telegram_status():
    return {
        "configured": telegram_bridge.is_configured(),
        "notify_mode": telegram_bridge.notify_mode,
    }


@app.post("/api/telegram/test", status_code=204)
async def telegram_test():
    if not telegram_bridge.is_configured():
        raise HTTPException(400, "未配置 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID")
    try:
        await telegram_bridge.send_test_message()
    except TelegramError as e:
        raise HTTPException(502, f"发送失败：{e}")


# ── 健康检查 ──────────────────────────────────────────────

@app.get("/api/health")
async def health():
    return {"status": "ok", "workspace": str(WORKSPACE_DIR)}


# ── 系统级 LLM（翻译等系统功能，不走池化配额） ────────────

@app.get("/api/system-llm/status")
async def system_llm_status():
    """前端据此决定是否显示翻译等入口。"""
    llm = SystemLLM.from_config(WORKSPACE_DIR)
    return {"configured": llm.is_configured(), "provider": llm.provider}


@app.post("/api/translate", response_model=TranslateResponse)
async def translate(req: TranslateRequest):
    if not req.text.strip():
        return TranslateResponse(translated="")
    # 内容寻址缓存命中即返回，跳过 LLM（抗刷新、跨端共享、省 token）
    cached = translation_cache.get(req.text, req.target)
    if cached is not None:
        return TranslateResponse(translated=cached, cached=True)

    llm = SystemLLM.from_config(WORKSPACE_DIR)
    if not llm.is_configured():
        raise HTTPException(status_code=503, detail="系统 LLM 未配置，请在 config.conf 设置 SYSTEM_LLM_*")
    try:
        translated = await translate_text(llm, req.text, req.target)
    except SystemLLMError as e:
        raise HTTPException(status_code=502, detail=str(e))
    if translated.strip():
        translation_cache.put(req.text, req.target, translated)
    return TranslateResponse(translated=translated, cached=False)


# ── 系统设置（config.conf 读写，由 settings_schema 登记表驱动） ──

@app.get("/api/config")
async def get_config():
    """按登记表返回分组配置与当前值（密钥脱敏）。"""
    cfg = _load_config(WORKSPACE_DIR)
    groups = []
    for g in SETTINGS_GROUPS:
        fields = []
        for f in g.fields:
            raw = cfg.get(f.key, "")
            fields.append({
                "key": f.key, "label": f.label, "placeholder": f.placeholder,
                "help": f.help, "secret": f.secret, "requires_apply": f.requires_apply,
                "options": list(f.options),
                "value": (mask_secret(raw) if f.secret else raw),
                "is_set": bool(raw.strip()),
            })
        groups.append({"id": g.id, "title": g.title, "help": g.help, "fields": fields})
    return {"groups": groups}


@app.put("/api/config")
async def update_config(req: ConfigUpdateRequest):
    """写入 config.conf。只接受登记表中的键；密钥留空表示保持不变。"""
    saved: list[str] = []
    requires_apply = False
    for key, value in req.updates.items():
        f = field_for(key)
        if f is None:
            raise HTTPException(status_code=400, detail=f"未知配置项: {key}")
        value = (value or "").strip()
        if any(ch.isspace() for ch in value):
            raise HTTPException(status_code=400, detail=f"{f.key} 的值不能包含空格")
        if f.options and value and value not in f.options:
            raise HTTPException(status_code=400, detail=f"{f.key} 取值须为 {list(f.options)} 之一")
        if f.secret and value == "":
            continue  # 密钥留空 = 保持不变
        _set_config(WORKSPACE_DIR, f.key, value)
        saved.append(f.key)
        requires_apply = requires_apply or f.requires_apply
    # config.conf 现在可能含密钥，收紧文件权限
    conf = WORKSPACE_DIR / "config.conf"
    if conf.exists():
        try:
            conf.chmod(0o600)
        except OSError:
            pass
    return {"saved": saved, "requires_apply": requires_apply}


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
    """列出所有账号，包含容器状态和忙碌状态。

    list_accounts() 内部按项目数逐个调用 docker inspect（同步子进程），丢进线程池执行，
    避免卡住事件循环。
    """
    return await run_in_threadpool(scheduler.list_accounts)


@app.post("/api/accounts/{name}/usage/refresh", response_model=AccountUsage)
async def refresh_account_usage(name: str):
    """立即探测一次指定账号的 Claude Max 套餐用量（而非等下一次后台轮询）"""
    try:
        return await scheduler.refresh_account_usage(name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


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
    if req.conversation_id and scheduler.conversation_queue_full(req.conversation_id):
        raise HTTPException(
            status_code=429,
            detail=f"队列已满，最多支持 {MAX_PENDING_PER_CONV} 条排队任务，请等待执行或删除队列中的任务后再发送",
        )

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
            model             = req.model,
            execute_at        = req.execute_at,
            parent_task_id = req.parent_task_id,
            depends_on     = req.depends_on,
            pipeline_id    = req.pipeline_id,
            board_card_id  = req.board_card_id,
            ephemeral      = req.ephemeral,
            execution_mode = req.execution_mode,
            secrets        = req.secrets,
            output_dir     = req.output_dir,
            ephemeral_retention = req.ephemeral_retention,
            ephemeral_ttl_minutes = req.ephemeral_ttl_minutes,
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
    board = scheduler.create_board(name)
    return BoardResponse.from_board(board)


@app.patch("/api/boards/{board_id}", response_model=BoardResponse)
async def update_board(board_id: str, req: BoardUpdateRequest):
    try:
        board = scheduler.update_board(board_id, req.name)
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
        BoardCardResponse.from_card(c, task_ids=scheduler.task_ids_for_board_card(c))
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
    return BoardCardResponse.from_card(card)  # 新卡片无关联任务，task_ids 为空


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
    return BoardCardResponse.from_card(card, task_ids=scheduler.task_ids_for_board_card(card))


@app.delete("/api/board-cards/{card_id}", status_code=204)
async def delete_board_card(card_id: str):
    try:
        scheduler.delete_board_card(card_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


def _list_projects_sync() -> list[ProjectResponse]:
    from coderfleet.server import docker_mgr as _dm

    accounts_by_name = {a.name: a for a in scheduler.get_accounts()}
    responses = []
    for p in scheduler.list_projects():
        resp = ProjectResponse.from_project(p)
        acc = accounts_by_name.get(p.account)
        if acc is not None:
            resp.container_running = _dm.is_container_running(p.container_name(acc.type))
        responses.append(resp)
    return responses


@app.get("/api/projects", response_model=list[ProjectResponse])
async def list_projects():
    # 每个项目都要调一次 docker inspect（同步子进程），丢进线程池执行，避免卡住事件循环。
    return await run_in_threadpool(_list_projects_sync)


def _validate_secondary_accounts(secondary_accounts: list[str], primary_account: str) -> None:
    accounts_by_name = {a.name: a for a in scheduler.get_accounts()}
    for n in secondary_accounts:
        if n not in accounts_by_name:
            raise HTTPException(status_code=404, detail=f"账号 '{n}' 不存在")
    primary_type = accounts_by_name[primary_account].type.value
    secondary_types = [accounts_by_name[n].type.value for n in secondary_accounts]
    dupes = duplicate_account_types([primary_type, *secondary_types])
    if dupes:
        raise HTTPException(
            status_code=400,
            detail=f"账号类型互斥：{' / '.join(dupes)} 类型的账号在同一项目中只能绑定一个",
        )


@app.post("/api/projects", status_code=201)
async def create_project(req: ProjectCreateRequest):
    _validate_identifier(req.name, "项目")
    _validate_ide_port(req.ide_enabled, req.ide_port)
    if any(p.name == req.name for p in scheduler.get_projects()):
        raise HTTPException(status_code=409, detail=f"项目 '{req.name}' 已存在")
    if not any(a.name == req.account for a in scheduler.get_accounts()):
        raise HTTPException(status_code=404, detail=f"账号 '{req.account}' 不存在")
    _validate_secondary_accounts(req.secondary_accounts, req.account)
    try:
        project = scheduler.save_project(req.name, req.account, req.path, req.active, req.ide_enabled, req.ide_port, req.ide_auth, req.ide_remote, req.image, req.docker_socket, req.secondary_accounts)
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
    new_image = existing.image if req.image is None else req.image
    new_docker_socket = existing.docker_socket if req.docker_socket is None else req.docker_socket
    new_secondary_accounts = existing.secondary_accounts if req.secondary_accounts is None else req.secondary_accounts
    if not new_ide_enabled:
        new_ide_port = None
    _validate_ide_port(new_ide_enabled, new_ide_port)
    if req.account and not any(a.name == new_account for a in scheduler.get_accounts()):
        raise HTTPException(status_code=404, detail=f"账号 '{new_account}' 不存在")
    _validate_secondary_accounts(new_secondary_accounts, new_account)
    try:
        project = scheduler.save_project(name, new_account, new_path, new_active, new_ide_enabled, new_ide_port, new_ide_auth, new_ide_remote, new_image, new_docker_socket, new_secondary_accounts)
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


def _resolve_project_and_account(name: str):
    project = scheduler.find_project_by_name(name)
    if project is None:
        raise HTTPException(status_code=404, detail=f"项目 '{name}' 不存在")
    acc = next((a for a in scheduler.get_accounts() if a.name == project.account), None)
    if acc is None:
        raise HTTPException(status_code=404, detail=f"账号 '{project.account}' 不存在")
    return project, acc


@app.get("/api/projects/{name}/container/status")
async def get_project_container_status(name: str):
    project, acc = _resolve_project_and_account(name)
    from coderfleet.server import docker_mgr as _dm
    running = _dm.is_container_running(project.container_name(acc.type))
    return {"running": running}


@app.post("/api/projects/{name}/container/start")
async def start_project_container(name: str):
    project, acc = _resolve_project_and_account(name)
    from coderfleet.compose import write_compose
    from coderfleet.docker_ops import start_services

    service = project.service_name(acc.type)
    await asyncio.get_event_loop().run_in_executor(None, write_compose, WORKSPACE_DIR)
    result = await asyncio.get_event_loop().run_in_executor(
        None, start_services, WORKSPACE_DIR, [service]
    )
    if result.returncode != 0:
        raise HTTPException(status_code=500, detail=f"启动项目 '{name}' 失败")
    return {"ok": True}


@app.post("/api/projects/{name}/container/stop")
async def stop_project_container(name: str):
    project, acc = _resolve_project_and_account(name)
    from coderfleet.docker_ops import stop_services

    service = project.service_name(acc.type)
    result = await asyncio.get_event_loop().run_in_executor(
        None, stop_services, WORKSPACE_DIR, [service]
    )
    if result.returncode != 0:
        raise HTTPException(status_code=500, detail=f"停止项目 '{name}' 失败")
    return {"ok": True}


# ── 项目镜像管理 ──────────────────────────────────────────

def _project_dockerfile_path(name: str) -> Path:
    return WORKSPACE_DIR / "projects" / name / "Dockerfile"


def _default_project_image(name: str) -> str:
    return f"coderfleet-{name}:latest"


class DockerfileUpdateRequest(BaseModel):
    content: str


class ImageBuildRequest(BaseModel):
    image_tag: str = ""
    build_id: str = ""


@app.get("/api/projects/{name}/dockerfile")
async def get_project_dockerfile(name: str):
    if not any(p.name == name for p in scheduler.get_projects()):
        raise HTTPException(status_code=404, detail=f"项目 '{name}' 不存在")
    path = _project_dockerfile_path(name)
    content = path.read_text(encoding="utf-8") if path.exists() else ""
    return {"content": content}


@app.put("/api/projects/{name}/dockerfile", status_code=200)
async def save_project_dockerfile(name: str, req: DockerfileUpdateRequest):
    if not any(p.name == name for p in scheduler.get_projects()):
        raise HTTPException(status_code=404, detail=f"项目 '{name}' 不存在")
    path = _project_dockerfile_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(req.content, encoding="utf-8")
    return {"ok": True}


@app.post("/api/projects/{name}/image/build")
async def build_project_image(name: str, req: ImageBuildRequest = None):
    """触发 docker build 构建项目专属镜像。

    构建本身通过 asyncio.create_task 在后台运行，与本次 HTTP 请求的生命周期无关——
    调用方（浏览器）断开连接不会中断构建，只是不再收到这次响应里的输出。响应体仍是
    一段纯文本流（tail 构建日志文件），保持与旧版一致的前端消费方式；构建记录与完整
    日志落盘在 builds/ 下，随时可通过 /api/builds/{build_id} 系列接口回看。

    req.image_tag 优先；未提供则用 projects.conf 中的 IMAGE=，再 fallback 到自动推导名。
    构建成功后始终将最终镜像名写入 projects.conf。
    """
    if req is None:
        req = ImageBuildRequest()
    existing = next((p for p in scheduler.get_projects() if p.name == name), None)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"项目 '{name}' 不存在")

    dockerfile = _project_dockerfile_path(name)
    if not dockerfile.exists():
        raise HTTPException(status_code=400, detail="Dockerfile 不存在，请先保存 Dockerfile")

    image_tag = req.image_tag.strip() or existing.image or _default_project_image(name)
    build_id = req.build_id.strip() or uuid.uuid4().hex
    cfg = _load_config(WORKSPACE_DIR)
    platform = cfg.get("BUILD_PLATFORM", "linux/amd64")

    build = ImageBuild(id=build_id, kind="project", project_name=name, image_tag=image_tag, triggered_by="web")
    build.save(scheduler.builds_dir)

    def _on_success(tag: str) -> None:
        if tag != existing.image:
            scheduler.save_project(
                name, existing.account, existing.path,
                existing.active, existing.ide_enabled, existing.ide_port,
                existing.ide_auth, existing.ide_remote, tag,
                existing.docker_socket,
            )
            with scheduler.get_build_log_path(build_id).open("a", encoding="utf-8") as f:
                f.write(f"  已写入 IMAGE={tag} 到 projects.conf\n")

    asyncio.create_task(run_image_build(
        build, scheduler.builds_dir, dockerfile, dockerfile.parent, platform,
        image_builds, on_success=_on_success,
    ))

    return StreamingResponse(
        _tail_build_log_plain(build_id), media_type="text/plain; charset=utf-8",
    )


@app.delete("/api/projects/{name}/image/build/{build_id}", status_code=200)
async def cancel_project_image_build(name: str, build_id: str):
    """停止正在执行的项目镜像构建。"""
    record = image_builds.get(build_id)
    if record is not None and record.project_name != name:
        raise HTTPException(status_code=404, detail="没有正在构建的镜像")
    result = await image_builds.cancel(build_id)
    return {"ok": result.cancelled, "message": result.message}


class SharedImageBuildRequest(BaseModel):
    build_id: str = ""


@app.post("/api/image/build")
async def build_shared_image(req: SharedImageBuildRequest = None):
    """触发 docker build 构建共享镜像。

    与项目专属镜像构建（build_project_image）共用同一套 run_image_build /
    builds/ 落盘逻辑：后台运行、断线不中断、记录进同一份构建历史，kind="shared"
    与项目专属构建区分开。
    """
    if req is None:
        req = SharedImageBuildRequest()
    dockerfile = WORKSPACE_DIR / "Dockerfile"
    if not dockerfile.exists():
        raise HTTPException(status_code=400, detail="共享 Dockerfile 不存在")

    cfg = _load_config(WORKSPACE_DIR)
    platform = cfg.get("BUILD_PLATFORM", "linux/amd64")
    image_tag = f"{cfg.get('IMAGE_NAME', 'coderfleet')}:{cfg.get('IMAGE_TAG', 'latest')}"
    build_id = req.build_id.strip() or uuid.uuid4().hex

    build = ImageBuild(id=build_id, kind="shared", project_name="", image_tag=image_tag, triggered_by="web")
    build.save(scheduler.builds_dir)

    asyncio.create_task(run_image_build(
        build, scheduler.builds_dir, dockerfile, WORKSPACE_DIR, platform, image_builds,
    ))

    return StreamingResponse(
        _tail_build_log_plain(build_id), media_type="text/plain; charset=utf-8",
    )


@app.delete("/api/image/build/{build_id}", status_code=200)
async def cancel_shared_image_build(build_id: str):
    """停止正在执行的共享镜像构建。"""
    result = await image_builds.cancel(build_id)
    return {"ok": result.cancelled, "message": result.message}


# ── 镜像构建历史 ──────────────────────────────────────────

async def _iter_build_log_chunks(build_id: str, start_offset: int = 0) -> AsyncIterator[bytes]:
    """轮询 builds/{id}.log 的字节增量，直到构建结束才停止。

    只读文件，不碰子进程——这是"查看构建"和"驱动构建"解耦之后唯一的连接点：
    调用方（无论是本次触发构建的响应，还是历史面板的重连）断开都不影响构建本身。
    """
    log_path = scheduler.get_build_log_path(build_id)
    last_size = start_offset
    while True:
        if log_path.exists():
            cur_size = log_path.stat().st_size
            if cur_size > last_size:
                async with aiofiles.open(log_path, "rb") as f:
                    await f.seek(last_size)
                    data = await f.read()
                last_size = cur_size
                yield data
        build = scheduler.get_build(build_id)
        if build is None or build.status != ImageBuildStatus.running:
            return
        await asyncio.sleep(0.3)


async def _tail_build_log_plain(build_id: str) -> AsyncIterator[str]:
    async for chunk in _iter_build_log_chunks(build_id):
        yield chunk.decode("utf-8", errors="replace")


async def _tail_build_log_sse(build_id: str, start_offset: int = 0) -> AsyncIterator[str]:
    async for chunk in _iter_build_log_chunks(build_id, start_offset):
        for line in chunk.decode("utf-8", errors="replace").splitlines(keepends=True):
            yield f"data: {line.rstrip()}\n\n"
    yield "data: [DONE]\n\n"


@app.get("/api/builds")
async def list_builds(
    project: Optional[str] = None,
    kind: Optional[str] = None,
    limit: int = Query(50, ge=1, le=500),
):
    builds = scheduler.list_builds(project_name=project, kind=kind)
    return builds[:limit]


@app.get("/api/builds/{build_id}")
async def get_build(build_id: str):
    build = scheduler.get_build(build_id)
    if build is None:
        raise HTTPException(status_code=404, detail=f"构建记录 '{build_id}' 不存在")
    return build


@app.get("/api/builds/{build_id}/logs", response_class=PlainTextResponse)
async def get_build_logs(build_id: str):
    log_path = scheduler.get_build_log_path(build_id)
    if not log_path.exists():
        raise HTTPException(status_code=404, detail=f"日志文件不存在：{build_id}")
    return log_path.read_text(encoding="utf-8")


@app.get("/api/builds/{build_id}/logs/stream")
async def stream_build_logs(
    build_id: str,
    skip_bytes: int = Query(0, ge=0, description="客户端已获取的字节偏移量，从此处开始推送剩余内容"),
):
    """Server-Sent Events 实时构建日志流，可对已结束的构建重放全部日志（skip_bytes=0）。"""
    if scheduler.get_build(build_id) is None:
        raise HTTPException(status_code=404, detail=f"构建记录 '{build_id}' 不存在")
    return StreamingResponse(
        _tail_build_log_sse(build_id, skip_bytes),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )


@app.delete("/api/projects/{name}/image", status_code=200)
async def clear_project_image(name: str):
    """清除项目专属镜像配置，恢复使用共享镜像。"""
    existing = next((p for p in scheduler.get_projects() if p.name == name), None)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"项目 '{name}' 不存在")
    scheduler.save_project(
        name, existing.account, existing.path,
        existing.active, existing.ide_enabled, existing.ide_port,
        existing.ide_auth, existing.ide_remote, "",
        existing.docker_socket,
    )
    return {"ok": True}


# ── 系统运维 ──────────────────────────────────────────────

@app.post("/api/system/apply")
async def system_apply(full: bool = False):
    """重新生成 docker-compose.yml 并同步容器，SSE 流式输出进度。

    默认增量同步（只影响新增/变更的项目，不影响其他正在运行的容器）；
    full=true 时走旧的全量销毁重建（会中断所有正在运行的会话）。
    """
    from coderfleet.compose import write_compose
    from coderfleet.docker_ops import _dc

    async def _stream() -> AsyncIterator[str]:
        try:
            yield ">>> 生成 docker-compose.yml...\n"
            await asyncio.get_event_loop().run_in_executor(None, write_compose, WORKSPACE_DIR)
            yield "✓ docker-compose.yml 已生成\n\n"

            dc = _dc(WORKSPACE_DIR)

            if full:
                yield "⚠ 全量重建：所有正在运行的会话都会被中断\n\n"
                yield ">>> 停止旧容器 (down --remove-orphans)...\n"
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
            else:
                yield ">>> 同步容器状态 (up -d --remove-orphans，仅新增/变更的项目会受影响)...\n"
                proc = await asyncio.create_subprocess_exec(
                    *dc, "up", "-d", "--remove-orphans",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                )

            async for line in proc.stdout:
                yield line.decode("utf-8", errors="replace")
            rc = await proc.wait()

            if rc != 0:
                yield f"\n✗ 容器启动失败（exit={rc}）\n"
            else:
                yield "\n✓ 完成！\n"
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


# ── 项目文件浏览 ──────────────────────────────────────────

class FileEntry(BaseModel):
    name: str
    path: str
    is_dir: bool
    size: Optional[int]
    modified: Optional[float]


_FILES_SKIP = {'.git', '__pycache__', 'node_modules', '.DS_Store', '.mypy_cache', '.ruff_cache'}


def _file_entry_for(base: Path, target: Path, is_dir: bool) -> Optional[FileEntry]:
    try:
        stat = target.stat()
    except OSError:
        return None
    return FileEntry(
        name=target.name,
        path=str(target.relative_to(base)),
        is_dir=is_dir,
        size=stat.st_size if not is_dir else None,
        modified=stat.st_mtime,
    )


@app.get("/api/projects/{project_name}/files", response_model=list[FileEntry])
async def list_project_files(
    project_name: str,
    path: str = Query("", description="相对路径，空表示根目录"),
):
    """列出项目目录下的文件和子目录（单层）。"""
    project = scheduler.find_project_by_name(project_name)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    base = Path(project.path).resolve()
    target = (base / path).resolve() if path else base
    try:
        target.relative_to(base)
    except ValueError:
        raise HTTPException(status_code=403, detail="路径不合法")
    if not target.is_dir():
        raise HTTPException(status_code=400, detail="不是目录")
    entries: list[FileEntry] = []
    try:
        items = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        for item in items:
            if item.name in _FILES_SKIP:
                continue
            entry = _file_entry_for(base, item, item.is_dir())
            if entry is not None:
                entries.append(entry)
    except PermissionError:
        pass
    return entries


_FILES_SEARCH_MAX_WALK = 20000


@app.get("/api/projects/{project_name}/files/search", response_model=list[FileEntry])
async def search_project_files(
    project_name: str,
    q: str = Query("", description="搜索关键字，匹配完整相对路径"),
    limit: int = Query(30, ge=1, le=200),
):
    """递归搜索项目文件/目录，供聊天输入框 @ 提及自动补全使用。"""
    project = scheduler.find_project_by_name(project_name)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    base = Path(project.path).resolve()

    collected: list[tuple[str, bool]] = []
    for root, dirnames, filenames in os.walk(base):
        dirnames[:] = sorted(d for d in dirnames if d not in _FILES_SKIP)
        rel_root = Path(root).relative_to(base)
        for name in dirnames:
            rel = name if rel_root == Path('.') else str(rel_root / name)
            collected.append((rel, True))
        for name in filenames:
            if name in _FILES_SKIP:
                continue
            rel = name if rel_root == Path('.') else str(rel_root / name)
            collected.append((rel, False))
        if len(collected) >= _FILES_SEARCH_MAX_WALK:
            break

    collected.sort(key=lambda item: (not item[1], item[0].lower()))
    is_dir_by_path = dict(collected)
    ranked = rank_paths([path for path, _ in collected], q, limit=limit)

    # 多条目端点：与单目标端点（list_project_files/preview_project_file）不同，一次响应
    # 涉及许多条目，某个条目（如指向外部的符号链接）逃出项目根目录时，跳过它而不是
    # 用 403 拒绝整个搜索请求。
    entries: list[FileEntry] = []
    for rel in ranked:
        target = (base / rel).resolve()
        try:
            target.relative_to(base)
        except ValueError:
            continue
        is_dir = is_dir_by_path.get(rel, target.is_dir())
        entry = _file_entry_for(base, target, is_dir)
        if entry is not None:
            entries.append(entry)
    return entries


@app.get("/api/projects/{project_name}/preview")
async def preview_project_file(
    project_name: str,
    path: str = Query(..., description="相对路径"),
):
    """返回文件内容用于预览：图片直接响应，文本返回 JSON {content, truncated, name}。"""
    project = scheduler.find_project_by_name(project_name)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    base = Path(project.path).resolve()
    target = (base / path).resolve()
    try:
        target.relative_to(base)
    except ValueError:
        raise HTTPException(status_code=403, detail="路径不合法")
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    img_exts = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg', '.ico', '.bmp'}
    if target.suffix.lower() in img_exts:
        return FileResponse(target)
    MAX = 512 * 1024
    try:
        raw = target.read_bytes()
        if b'\x00' in raw[:1024]:
            raise HTTPException(status_code=400, detail="binary")
        text = raw[:MAX].decode('utf-8', errors='replace')
        return {"content": text, "truncated": len(raw) > MAX, "name": target.name}
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=400, detail="无法预览")


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
    # list_conversations() 是同步磁盘扫描（逐个会话 JSON 文件 stat + 解析），
    # 丢进线程池执行，避免会话/任务较多时卡住事件循环上其他并发请求
    # （SSE 日志流、WebSocket 终端、心跳轮询等）。
    convs = await run_in_threadpool(scheduler.list_conversations, include_archived=include_archived)
    return [ConversationResponse.from_conversation(c) for c in convs]


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


@app.post("/api/conversations/terminal", response_model=ConversationResponse, status_code=201)
async def create_terminal_conversation(req: TerminalConversationCreateRequest):
    """Create a new terminal-mode conversation backed by a persistent tmux session."""
    try:
        conv = scheduler.create_terminal_conversation(
            name         = req.name,
            project_name = req.project_name,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    return ConversationResponse.from_conversation(conv)


async def _replay_terminal_log(websocket: WebSocket, log_path: "Path", lines: int = 300) -> None:
    """Send the last N lines of the terminal transcript to a newly attached client."""
    try:
        if not log_path.exists():
            return
        text = log_path.read_text(encoding="utf-8", errors="replace")
        tail = "\r\n".join(text.splitlines()[-lines:])
        if tail:
            await websocket.send_json({"type": "history", "data": tail + "\r\n"})
    except Exception:
        pass


@app.websocket("/api/conversations/{conv_id}/terminal")
async def conversation_terminal(websocket: WebSocket, conv_id: str):
    """Attach to the persistent tmux session for a terminal-mode conversation."""
    if not _is_allowed_terminal_origin(
        websocket.headers.get("origin"),
        websocket.headers.get("host"),
    ):
        await websocket.close(code=1008)
        return

    await websocket.accept()
    session: TmuxTerminalSession | None = None

    try:
        conv = scheduler.get_conversation(conv_id)
        if conv is None or conv.mode != ConversationMode.terminal:
            await websocket.send_json({
                "type": "status", "state": "error",
                "message": "非终端对话或会话不存在",
            })
            await websocket.close(code=1008)
            return

        project = scheduler.find_project_by_name(conv.project_name)
        if project is None:
            await websocket.send_json({
                "type": "status", "state": "error",
                "message": f"项目 '{conv.project_name}' 不存在",
            })
            await websocket.close(code=1008)
            return

        from coderfleet.server.models import AccountType as _AT
        acc = next((a for a in scheduler.get_accounts() if a.name == conv.account), None)
        if acc is None:
            await websocket.send_json({
                "type": "status", "state": "error",
                "message": f"账号 '{conv.account}' 不存在",
            })
            await websocket.close(code=1008)
            return

        container_name = project.container_name(acc.type)
        from coderfleet.server import docker_mgr as _dm
        if not _dm.is_container_running(container_name):
            await websocket.send_json({
                "type": "status", "state": "error",
                "message": f"容器 {container_name} 未运行，请先启动容器",
            })
            await websocket.close(code=1008)
            return

        workdir = scheduler.container_workdir_for_project(project, project.path)
        log_path_in_container = f"/workspace/.coderfleet-terminals/{conv.id}.log"
        host_log_path = scheduler._get_project_root_by_name(conv.project_name) / ".coderfleet-terminals" / f"{conv.id}.log"

        # Replay transcript before attaching so user sees prior history
        await _replay_terminal_log(websocket, host_log_path)

        # Ensure tmux session exists and pipe-pane is active
        try:
            await setup_tmux_session(
                container_name        = container_name,
                session_name          = conv.tmux_session,
                workdir               = workdir,
                log_path_in_container = log_path_in_container,
            )
        except Exception as e:
            await websocket.send_json({
                "type": "status", "state": "error",
                "message": f"初始化 tmux 会话失败：{e}",
            })
            await websocket.close(code=1008)
            return

        session = TmuxTerminalSession(
            container_name = container_name,
            session_name   = conv.tmux_session,
            workdir        = workdir,
        )
        session.start()
        conv.touch(scheduler.conversations_dir)

        await websocket.send_json({
            "type": "status",
            "state": "connected",
            "message": f"已连接 {container_name} · tmux:{conv.tmux_session}",
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

        async def pump_input() -> None:
            assert session is not None
            while True:
                raw = await websocket.receive_text()
                try:
                    message = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                msg_type = message.get("type")
                if msg_type == "input":
                    session.write(str(message.get("data", "")))
                elif msg_type == "resize":
                    try:
                        cols = int(message.get("cols", 0))
                        rows = int(message.get("rows", 0))
                    except (TypeError, ValueError):
                        continue
                    session.resize(cols=cols, rows=rows)

        output_task = asyncio.create_task(pump_output())
        input_task  = asyncio.create_task(pump_input())
        done, pending = await asyncio.wait(
            {output_task, input_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for t in pending:
            t.cancel()
        for t in done:
            t.result()

    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await websocket.send_json({
                "type": "status", "state": "error", "message": str(e),
            })
        except Exception:
            pass
    finally:
        if session is not None:
            session.close()   # detach, not kill
        try:
            await websocket.send_json({
                "type": "status", "state": "disconnected",
                "message": "已断开连接（tmux 会话继续运行）",
            })
        except Exception:
            pass


@app.get("/api/conversations/{conv_id}/terminal/status")
async def terminal_conversation_status(conv_id: str):
    """Check whether the backing tmux session is alive."""
    conv = scheduler.get_conversation(conv_id)
    if conv is None or conv.mode != ConversationMode.terminal:
        raise HTTPException(status_code=404, detail="非终端对话")
    project = scheduler.find_project_by_name(conv.project_name)
    if project is None:
        raise HTTPException(status_code=404, detail=f"项目 '{conv.project_name}' 不存在")
    acc = next((a for a in scheduler.get_accounts() if a.name == conv.account), None)
    if acc is None:
        raise HTTPException(status_code=404, detail=f"账号 '{conv.account}' 不存在")
    container_name = project.container_name(acc.type)
    alive = await is_tmux_session_alive(container_name, conv.tmux_session)
    return {"alive": alive, "tmux_session": conv.tmux_session, "container": container_name}


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


@app.delete("/api/sessions/{conversation_id}", status_code=204)
async def delete_session(conversation_id: str):
    """关闭 ephemeral 会话容器并删除本地 workspace 目录（sessions/<conv-id>/）。"""
    scheduler.close_ephemeral_conversation(conversation_id)
    scheduler.delete_session(conversation_id)


# ── 任务列表 ──────────────────────────────────────────────

@app.get("/api/tasks", response_model=list[TaskResponse])
async def list_tasks(
    status:  Optional[str] = Query(None, description="按状态过滤：running/done/failed/killed"),
    account: Optional[str] = Query(None, description="按账号名过滤"),
    conversation_id: Optional[str] = Query(None, description="按所属会话过滤"),
    limit:   int           = Query(50,   description="返回条数上限"),
    include_archived: bool = Query(False, description="是否包含已归档的任务"),
):
    # 同上：list_tasks() 会扫描 tasks/ 目录下的全部任务文件，丢进线程池执行。
    tasks = await run_in_threadpool(scheduler.list_tasks)

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

    if conversation_id:
        tasks = [t for t in tasks if t.conversation_id == conversation_id]

    return [TaskResponse.from_task(t) for t in tasks[:limit]]


@app.get("/api/tasks/heartbeat", response_model=list[TaskHeartbeat])
async def tasks_heartbeat():
    """给前端高频轮询用的瘦身端点：只探测状态变化，避免每 5 秒都拉全量任务详情。"""
    tasks = await run_in_threadpool(scheduler.list_tasks)
    return [TaskHeartbeat.from_task(t) for t in tasks if not getattr(t, "archived", False)]


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
    archived: Optional[bool] = None
    prompt: Optional[str] = None


@app.patch("/api/tasks/{task_id}", response_model=TaskResponse)
async def update_task(task_id: str, body: TaskUpdate):
    if body.prompt is not None:
        try:
            task = scheduler.update_task_prompt(task_id, body.prompt)
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except RuntimeError as e:
            raise HTTPException(status_code=409, detail=str(e))
        return TaskResponse.from_task(task)
    if body.archived is not None:
        try:
            task = scheduler.archive_task(task_id, body.archived)
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))
        return TaskResponse.from_task(task)
    raise HTTPException(status_code=400, detail="请提供 archived 或 prompt 字段")


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
            model          = getattr(original, "model", ""),
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
    # 日志文件可达数 MB，同步读取会独占单进程事件循环，挤占同一时刻的其它请求
    # （SSE 日志流、心跳轮询等）——丢进线程池执行。
    return await run_in_threadpool(log_path.read_text, encoding="utf-8")


@app.get("/api/tasks/{task_id}/output")
async def get_task_output(task_id: str):
    """提取任务的结构化输出文本（由 log_parser 解析）。"""
    from coderfleet.server.log_parser import extract_task_output
    task = scheduler.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"任务 '{task_id}' 不存在")
    log_path = scheduler.get_log_path(task_id)
    if not log_path.exists():
        return {"text": ""}
    log_text = await run_in_threadpool(log_path.read_text, encoding="utf-8", errors="ignore")
    text = extract_task_output(log_text, task.type.value)
    return {"text": text}


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

# NOTE: v1「手动创建流水线 / 手动加任务」端点已下线（见 #17）。
# 工作流一律经模板运行产生 WorkflowRun；下方仅保留只读/删除/恢复端点以兼容历史数据。
# 执行引擎脱离 Pipeline 并删除模型类的工作见 #28。


@app.get("/api/pipelines", response_model=list[PipelineResponse])
async def list_pipelines():
    pipelines = scheduler.list_pipelines()
    all_tasks = {t.id: t for t in scheduler.list_tasks()}
    return [
        PipelineResponse.from_pipeline(p, [all_tasks[tid] for tid in p.task_ids if tid in all_tasks])
        for p in pipelines
    ]


@app.get("/api/pipelines/{pipeline_id}", response_model=PipelineResponse)
async def get_pipeline(pipeline_id: str):
    pipeline = scheduler.get_pipeline(pipeline_id)
    if pipeline is None:
        raise HTTPException(status_code=404, detail=f"工作流 '{pipeline_id}' 不存在")
    all_tasks = {t.id: t for t in scheduler.list_tasks()}
    tasks = [all_tasks[tid] for tid in pipeline.task_ids if tid in all_tasks]
    return PipelineResponse.from_pipeline(pipeline, tasks)


@app.delete("/api/pipelines/{pipeline_id}", status_code=204)
async def delete_pipeline(pipeline_id: str):
    try:
        scheduler.delete_pipeline(pipeline_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/api/pipelines/{pipeline_id}/resume", response_model=PipelineResponse)
async def resume_pipeline(pipeline_id: str):
    try:
        pipeline = await scheduler.resume_pipeline(pipeline_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    all_tasks = {t.id: t for t in scheduler.list_tasks()}
    tasks = [all_tasks[tid] for tid in pipeline.task_ids if tid in all_tasks]
    return PipelineResponse.from_pipeline(pipeline, tasks)


def _workflow_run_response(run: WorkflowRun) -> WorkflowRunResponse:
    # 按需精确查找这次运行涉及的任务，而不是 scheduler.list_tasks() 把 tasks/ 目录下的全部
    # 任务加载一遍——get_task() 是单文件路径读取，开销只随本次运行的节点数增长。
    pipeline = scheduler.get_pipeline(run.legacy_pipeline_id) if run.legacy_pipeline_id else None
    task_ids = [n.task_id for n in run.node_executions if n.task_id]
    tasks = [t for t in (scheduler.get_task(tid) for tid in task_ids) if t is not None]
    return WorkflowRunResponse.from_run(
        run,
        tasks,
        default_project=getattr(pipeline, "default_project", ""),
        last_error=getattr(pipeline, "last_error", ""),
    )


def _schedule_run_response(sched: Schedule, run_type: str, run_obj) -> ScheduleRunResponse:
    if run_type == "workflow":
        run = scheduler.get_workflow_run_by_legacy_pipeline_id(run_obj.id)
        return ScheduleRunResponse(
            run_type="workflow",
            schedule=ScheduleResponse.from_schedule(sched),
            workflow_run=_workflow_run_response(run) if run else None,
        )
    return ScheduleRunResponse(
        run_type="task",
        schedule=ScheduleResponse.from_schedule(sched),
        task=TaskResponse.from_task(run_obj),
    )


def _list_workflow_runs_sync() -> list[WorkflowRunResponse]:
    return [_workflow_run_response(r) for r in scheduler.list_workflow_runs()]


@app.get("/api/workflow-runs", response_model=list[WorkflowRunResponse])
async def list_workflow_runs():
    # 同上：list_workflow_runs() 会扫描 workflow_runs/ 及 pipelines/ 目录下的全部记录，丢进线程池执行。
    return await run_in_threadpool(_list_workflow_runs_sync)


def _get_workflow_run_sync(run_id: str) -> Optional[WorkflowRunResponse]:
    run = scheduler.get_workflow_run(run_id)
    if run is None:
        return None
    return _workflow_run_response(run)


@app.get("/api/workflow-runs/{run_id}", response_model=WorkflowRunResponse)
async def get_workflow_run(run_id: str):
    response = await run_in_threadpool(_get_workflow_run_sync, run_id)
    if response is None:
        raise HTTPException(status_code=404, detail=f"工作流运行 '{run_id}' 不存在")
    return response


@app.delete("/api/workflow-runs/{run_id}", status_code=204)
async def delete_workflow_run(run_id: str):
    try:
        scheduler.delete_workflow_run(run_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/api/workflow-runs/{run_id}/resume", response_model=WorkflowRunResponse)
async def resume_workflow_run(run_id: str):
    run = scheduler.get_workflow_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"工作流运行 '{run_id}' 不存在")
    if not run.legacy_pipeline_id:
        raise HTTPException(status_code=400, detail="此工作流运行没有兼容执行记录，无法恢复")
    try:
        pipeline = await scheduler.resume_pipeline(run.legacy_pipeline_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    refreshed = scheduler.get_workflow_run_by_legacy_pipeline_id(pipeline.id)
    if refreshed is None:
        raise HTTPException(status_code=404, detail=f"工作流运行 '{run_id}' 不存在")
    return _workflow_run_response(refreshed)


@app.post("/api/workflow-runs/{run_id}/cancel", response_model=WorkflowRunResponse)
async def cancel_workflow_run(run_id: str):
    run = scheduler.get_workflow_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"工作流运行 '{run_id}' 不存在")
    try:
        updated_run = await scheduler.cancel_workflow_run(run_id)
    except (ValueError, RuntimeError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _workflow_run_response(updated_run)


@app.post("/api/workflow-runs/{run_id}/approve", response_model=WorkflowRunResponse)
async def approve_workflow_run(run_id: str, token: str = ""):
    """批准工作流中的人工审批节点，工作流继续执行。"""
    try:
        updated_run = await scheduler.approve_workflow_run(run_id, token)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return _workflow_run_response(updated_run)


# ══════════════════════════════════════════════════════════════
#  定时计划 CRUD
# ══════════════════════════════════════════════════════════════

@app.get("/api/schedules", response_model=list[ScheduleResponse])
async def list_schedules():
    return [ScheduleResponse.from_schedule(s) for s in scheduler.list_schedules()]


@app.post("/api/schedules", response_model=ScheduleResponse, status_code=201)
async def create_schedule(req: ScheduleCreateRequest):
    sched = Schedule(
        id              = scheduler.new_schedule_id(),
        name            = req.name,
        prompt          = req.prompt,
        project_name    = req.project_name,
        target_type     = req.target_type,
        template_id     = req.template_id,
        workflow_input  = req.workflow_input,
        project_map     = req.project_map,
        default_project = req.default_project,
        default_account = req.default_account,
        workspace_policy = req.workspace_policy,
        schedule_type   = req.schedule_type,
        time_of_day     = req.time_of_day,
        days_of_week    = req.days_of_week,
        minute_of_hour  = req.minute_of_hour,
        cron_expr       = req.cron_expr,
        enabled         = req.enabled,
        auto            = req.auto,
        account         = req.account,
        execution_mode  = req.execution_mode,
        output_dir      = req.output_dir,
        ephemeral_retention = req.ephemeral_retention,
        ephemeral_ttl_minutes = req.ephemeral_ttl_minutes,
        webhook_enabled = req.webhook_enabled,
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
    # Use exclude_unset so fields not sent by the client are skipped,
    # but explicitly sent null/None values (e.g. clearing cron_expr) are preserved.
    updates = req.model_dump(exclude_unset=True)
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


@app.post("/api/schedules/{sched_id}/run-now", response_model=ScheduleRunResponse, status_code=201)
async def run_schedule_now(sched_id: str):
    sched = scheduler.get_schedule(sched_id)
    if sched is None:
        raise HTTPException(status_code=404, detail=f"定时计划 '{sched_id}' 不存在")
    run_type, run_obj = await scheduler._run_schedule_target(sched)
    sched.last_run_at = datetime.now().isoformat(timespec="seconds")
    sched.last_run_type = run_type
    if run_type == "workflow":
        sched.last_workflow_run_id = run_obj.id
        sched.last_task_id = ""
    else:
        sched.last_task_id = run_obj.id
        sched.last_workflow_run_id = ""
    sched.updated = datetime.now().isoformat(timespec="seconds")
    sched.save(scheduler.schedules_dir)
    return _schedule_run_response(sched, run_type, run_obj)


@app.post("/api/webhooks/{webhook_token}/trigger", response_model=ScheduleRunResponse, status_code=201)
async def webhook_trigger(webhook_token: str):
    """公开端点（无需认证），通过 webhook_token 触发对应定时计划立即执行一次。"""
    sched = next(
        (s for s in scheduler.list_schedules()
         if getattr(s, "webhook_enabled", False) and s.webhook_token == webhook_token),
        None,
    )
    if sched is None:
        raise HTTPException(status_code=404, detail="webhook not found or disabled")
    run_type, run_obj = await scheduler._run_schedule_target(sched)
    sched.last_run_at  = datetime.now().isoformat(timespec="seconds")
    sched.last_run_type = run_type
    if run_type == "workflow":
        sched.last_workflow_run_id = run_obj.id
        sched.last_task_id = ""
    else:
        sched.last_task_id = run_obj.id
        sched.last_workflow_run_id = ""
    sched.updated      = datetime.now().isoformat(timespec="seconds")
    sched.save(scheduler.schedules_dir)
    return _schedule_run_response(sched, run_type, run_obj)


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


@app.post("/api/workflow-templates/{template_id}/run", response_model=WorkflowRunResponse, status_code=201)
async def run_workflow_template(template_id: str, req: TemplateRunRequest):
    try:
        pipeline = await scheduler.run_template(
            template_id     = template_id,
            input_str       = req.input,
            project_map     = req.project_map,
            default_project = req.default_project,
            default_account = req.default_account,
            workspace_policy = req.workspace_policy,
        )
        run = scheduler.get_workflow_run_by_legacy_pipeline_id(pipeline.id)
        if run is None:
            raise HTTPException(status_code=500, detail="工作流运行创建失败")
        return _workflow_run_response(run)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ══════════════════════════════════════════════════════════════
#  日报 Digest
# ══════════════════════════════════════════════════════════════

_DATE_RE = _re.compile(r"^\d{4}-\d{2}-\d{2}$")


@app.get("/api/digest/dates")
async def get_digest_dates():
    """Return all dates (YYYY-MM-DD) with task activity, newest first."""
    return list_active_dates(scheduler.tasks_dir)


@app.get("/api/digest/{date}", response_model=DailyDigest)
async def get_digest(date: str):
    if not _DATE_RE.match(date):
        raise HTTPException(status_code=400, detail="日期格式必须为 YYYY-MM-DD")

    digest = compute_daily_stats(date, scheduler.tasks_dir)

    saved = load_digest(date, DIGEST_DIR)
    if saved:
        digest.ai_summary  = saved.ai_summary
        digest.ai_task_id  = saved.ai_task_id
        digest.generated_at = saved.generated_at
        digest.status      = saved.status

    # Auto-finalize: if the generating task is now done, extract its output
    if digest.ai_task_id and digest.status == DigestStatus.generating:
        ai_task = scheduler.get_task(digest.ai_task_id)
        if ai_task and ai_task.status == TaskStatus.done:
            log_path = scheduler.get_log_path(digest.ai_task_id)
            if log_path.exists():
                from coderfleet.server.log_parser import parse_log
                output = parse_log(log_path.read_text(encoding="utf-8"), ai_task.type.value)
                if output.text:
                    digest.ai_summary   = output.text
                    digest.status       = DigestStatus.ready
                    digest.generated_at = datetime.now().isoformat(timespec="seconds")
                    record = saved or DailyDigest(date=date)
                    record.ai_summary   = digest.ai_summary
                    record.ai_task_id   = digest.ai_task_id
                    record.status       = DigestStatus.ready
                    record.generated_at = digest.generated_at
                    record.save(DIGEST_DIR)
        elif ai_task and ai_task.status in (TaskStatus.failed, TaskStatus.killed):
            digest.status = DigestStatus.error
            if saved:
                saved.status = DigestStatus.error
                saved.save(DIGEST_DIR)

    return digest


@app.post("/api/digest/{date}/generate")
async def trigger_digest_generation(date: str):
    if not _DATE_RE.match(date):
        raise HTTPException(status_code=400, detail="日期格式必须为 YYYY-MM-DD")

    stats = compute_daily_stats(date, scheduler.tasks_dir)
    if stats.total_done + stats.total_failed + stats.total_killed == 0:
        raise HTTPException(status_code=404, detail="该日期没有已完成的任务记录")

    projects = [p for p in scheduler.list_projects() if p.active]
    if not projects:
        raise HTTPException(status_code=503, detail="没有活跃项目，无法提交摘要任务")

    prompt = build_generate_prompt(stats)
    task = await scheduler.submit(
        prompt=prompt,
        project_name=projects[0].name,
        auto=True,
    )

    record = load_digest(date, DIGEST_DIR) or DailyDigest(date=date)
    record.ai_task_id = task.id
    record.status     = DigestStatus.generating
    record.updated    = datetime.now().isoformat(timespec="seconds")
    record.save(DIGEST_DIR)

    return {"task_id": task.id, "status": "generating"}


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
