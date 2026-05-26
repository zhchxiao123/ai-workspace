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
    AccountResponse,
    AccountType,
    Conversation,
    ConversationResponse,
    ConversationStatus,
    LogicalProject,
    MarketplaceInstallRequest,
    Pipeline,
    PipelineResponse,
    ProjectResponse,
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


class ConversationCreateRequest(BaseModel):
    """从已有任务创建任务链的请求体"""
    name:    str
    task_id: str  # 用该任务的 native_session_id 初始化任务链

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
    version     = "0.1.0",
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


@app.post("/api/push/subscribe", status_code=204)
async def push_subscribe(req: PushSubscribeRequest):
    push_manager.add_subscription(req.subscription)


@app.post("/api/push/unsubscribe", status_code=204)
async def push_unsubscribe(req: PushUnsubscribeRequest):
    push_manager.remove_subscription(req.endpoint)


# ── 健康检查 ──────────────────────────────────────────────

@app.get("/api/health")
async def health():
    return {"status": "ok", "workspace": str(WORKSPACE_DIR)}


# ── 账号 ──────────────────────────────────────────────────

@app.get("/api/accounts", response_model=list[AccountResponse])
async def list_accounts():
    """列出所有账号，包含容器状态和忙碌状态"""
    return scheduler.list_accounts()


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
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))

    return TaskResponse.from_task(task)


@app.get("/api/projects", response_model=list[ProjectResponse])
async def list_projects():
    return [
        ProjectResponse.from_project(p)
        for p in scheduler.list_projects()
    ]


# ── 图片上传 ──────────────────────────────────────────────

_ALLOWED_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}


@app.post("/api/uploads")
async def upload_image(
    file: UploadFile = File(...),
    project_name: str = Query(..., description="项目名称"),
):
    """上传图片到项目工作目录，返回容器内可访问的路径。"""
    project = scheduler.find_project_by_name(project_name)
    if project is None:
        raise HTTPException(status_code=404, detail=f"项目 '{project_name}' 不存在")

    original_name = file.filename or "upload"
    ext = Path(original_name).suffix.lower()
    if ext not in _ALLOWED_IMAGE_EXTS:
        raise HTTPException(status_code=400, detail=f"不支持的图片格式：{ext}")

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
    status: ConversationStatus


@app.patch("/api/conversations/{conversation_id}", response_model=ConversationResponse)
async def update_conversation_status(conversation_id: str, body: ConversationStatusUpdate):
    try:
        conv = scheduler.archive_conversation(conversation_id, body.status)
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
    task_id: str,
    tail:    int = Query(50, description="从末尾多少行开始推送"),
):
    """
    Server-Sent Events 实时日志流。

    协议：
      data: <日志行内容>\n\n
      data: [DONE]\n\n   ← 任务结束时发送，客户端可关闭连接
    """
    log_path = scheduler.get_log_path(task_id)

    async def generate() -> AsyncIterator[str]:
        # 先推送已有的末尾 N 行
        existing_lines: list[str] = []
        if log_path.exists():
            async with aiofiles.open(log_path, encoding="utf-8") as f:
                content = await f.read()
            existing_lines = content.splitlines(keepends=True)
            for line in existing_lines[-tail:]:
                yield f"data: {line.rstrip()}\n\n"

        # 如果任务已结束，直接结束流
        task = scheduler.get_task(task_id)
        if task is None:
            yield "data: [DONE]\n\n"
            return

        last_size = log_path.stat().st_size if log_path.exists() else 0

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
                    async with aiofiles.open(log_path, encoding="utf-8") as f:
                        await f.seek(last_size)
                        new_content = await f.read()
                    last_size = cur_size
                    for line in new_content.splitlines(keepends=True):
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
