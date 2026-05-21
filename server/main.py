"""
main.py — AICM 调度服务入口

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

from models import (
    AccountResponse,
    AccountType,
    Conversation,
    ConversationResponse,
    ConversationStatus,
    ProjectResponse,
    Task,
    TaskCreateRequest,
    TaskResponse,
    TaskStatus,
)
from scheduler import Scheduler
from terminal import TerminalSession, resolve_terminal_target


class ConversationCreateRequest(BaseModel):
    """从已有任务创建任务链的请求体"""
    name:    str
    task_id: str  # 用该任务的 native_session_id 初始化任务链

# ── 初始化 ────────────────────────────────────────────────

WORKSPACE_DIR = Path(os.environ.get("AICM_WORKSPACE", Path(__file__).parent.parent))
scheduler     = Scheduler(WORKSPACE_DIR)

STATIC_DIR = Path(__file__).parent / "static"
STATIC_DIR.mkdir(exist_ok=True)

app = FastAPI(
    title       = "AICM Scheduler API",
    description = "AI Code Manager 任务调度服务",
    version     = "0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins     = ["*"],
    allow_credentials = True,
    allow_methods     = ["*"],
    allow_headers     = ["*"],
)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.on_event("startup")
async def reconcile_tasks_on_startup():
    await scheduler.reconcile_running_tasks()
    scheduler.start_scheduling_loop()


@app.get("/", include_in_schema=False)
async def index():
    html = STATIC_DIR / "index.html"
    if not html.exists():
        return PlainTextResponse("Web UI not found. Run: ./aicm.sh server", status_code=404)
    return FileResponse(html)


# ── 健康检查 ──────────────────────────────────────────────

@app.get("/api/health")
async def health():
    return {"status": "ok", "workspace": str(WORKSPACE_DIR)}


# ── 账号 ──────────────────────────────────────────────────

@app.get("/api/accounts", response_model=list[AccountResponse])
async def list_accounts():
    """列出所有账号，包含容器状态和忙碌状态"""
    return scheduler.list_accounts()


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

    upload_dir = Path(project.path) / ".aicm-uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)

    file_id = uuid.uuid4().hex[:16]
    filename = f"{file_id}{ext}"
    save_path = upload_dir / filename

    content = await file.read()
    save_path.write_bytes(content)

    return {
        "container_path": f"/workspace/.aicm-uploads/{filename}",
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
    file_path = Path(project.path) / ".aicm-uploads" / safe_name
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


# ── 启动入口 ──────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host    = "0.0.0.0",
        port    = int(os.environ.get("AICM_PORT", 8765)),
        reload  = False,
        workers = 1,        # 单进程，scheduler 状态在内存里
    )
