from __future__ import annotations

import json
import re
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field


class TaskStatus(str, Enum):
    scheduled = "scheduled"
    pending   = "pending"
    running   = "running"
    done    = "done"
    failed  = "failed"
    killed  = "killed"


class ConversationStatus(str, Enum):
    active   = "active"
    archived = "archived"


class ConversationMode(str, Enum):
    chat     = "chat"
    terminal = "terminal"


class BoardCardStatus(str, Enum):
    planned    = "planned"
    todo       = "todo"
    running    = "running"
    review     = "review"
    done       = "done"
    parked     = "parked"


# AccountType 从账号类型注册表自动派生，添加新类型只需编辑注册表。
from coderfleet.account_type_registry import AccountType  # noqa: F401, E402


class AccountAuth(str, Enum):
    login = "login"
    env   = "env"


class AccountProxy(str, Enum):
    relay = "relay"
    off   = "off"


# ── 账号 ──────────────────────────────────────────────────

class Account(BaseModel):
    name:     str
    type:     AccountType
    auth:     AccountAuth = AccountAuth.login
    env_file: str = ""
    proxy:    AccountProxy = AccountProxy.relay


class Project(BaseModel):
    name:        str
    account:     str
    path:        str
    active:      bool = True
    ide_enabled: bool = False
    ide_port:    Optional[int] = None
    ide_auth:    str = "none"
    ide_remote:  bool = False
    image:       str = ""
    ephemeral:   bool = False
    git_url:     str = ""

    @property
    def mount_path(self) -> str:
        return "/workspace"

    def container_name(self, account_type: AccountType) -> str:
        return f"{account_type.value}-{self.name}"

    def service_name(self, account_type: AccountType) -> str:
        return f"{account_type.value}-project-{self.name}"


# ── 任务 ──────────────────────────────────────────────────

class Conversation(BaseModel):
    id:                str
    name:              str
    account:           str
    type:              AccountType
    project:           str
    project_name:      str = ""
    native_session_id: str = ""
    status:            ConversationStatus = ConversationStatus.active
    mode:              ConversationMode   = ConversationMode.chat
    tmux_session:      str = ""
    created:           str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    updated:           str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    last_task_id:      str = ""
    ephemeral:         bool = False
    output_dir:        str = ""
    ephemeral_retention: str = "release_on_finish"  # release_on_finish | keep_until_ttl | keep_until_manual_close
    ephemeral_ttl_minutes: int = 120
    ephemeral_container_name: str = ""
    ephemeral_expires_at: str = ""

    def save(self, conversations_dir: Path) -> None:
        conversations_dir.mkdir(parents=True, exist_ok=True)
        path = conversations_dir / f"{self.id}.json"
        path.write_text(
            json.dumps(self.model_dump(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: Path) -> "Conversation":
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(**data)

    @classmethod
    def load_all(cls, conversations_dir: Path) -> list["Conversation"]:
        if not conversations_dir.exists():
            return []
        conversations = []
        for p in sorted(conversations_dir.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
            try:
                conversations.append(cls.load(p))
            except Exception:
                pass
        return conversations

    def touch(
        self,
        conversations_dir: Path,
        native_session_id: Optional[str] = None,
        last_task_id: Optional[str] = None,
    ) -> None:
        if native_session_id:
            self.native_session_id = native_session_id
        if last_task_id:
            self.last_task_id = last_task_id
        self.updated = datetime.now().isoformat(timespec="seconds")
        self.save(conversations_dir)


class Board(BaseModel):
    id:      str
    name:    str
    project_name: str = ""
    created: str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    updated: str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    def save(self, boards_dir: Path) -> None:
        boards_dir.mkdir(parents=True, exist_ok=True)
        path = boards_dir / f"{self.id}.json"
        path.write_text(
            json.dumps(self.model_dump(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: Path) -> "Board":
        return cls(**json.loads(path.read_text(encoding="utf-8")))

    @classmethod
    def load_all(cls, boards_dir: Path) -> list["Board"]:
        if not boards_dir.exists():
            return []
        boards = []
        for p in sorted(boards_dir.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
            try:
                boards.append(cls.load(p))
            except Exception:
                pass
        return boards

    def touch(self, boards_dir: Path) -> None:
        self.updated = datetime.now().isoformat(timespec="seconds")
        self.save(boards_dir)


class BoardCard(BaseModel):
    id:              str
    board_id:        str
    title:           str
    description:     str = ""
    project_name:    str = ""
    status:          BoardCardStatus = BoardCardStatus.planned
    priority:        str = "normal"
    conversation_id: str = ""
    task_ids:        list[str] = Field(default_factory=list)
    pipeline_id:     str = ""
    archived:        bool = False
    created:         str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    updated:         str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    def save(self, cards_dir: Path) -> None:
        cards_dir.mkdir(parents=True, exist_ok=True)
        path = cards_dir / f"{self.id}.json"
        path.write_text(
            json.dumps(self.model_dump(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: Path) -> "BoardCard":
        return cls(**json.loads(path.read_text(encoding="utf-8")))

    @classmethod
    def load_all(cls, cards_dir: Path) -> list["BoardCard"]:
        if not cards_dir.exists():
            return []
        cards = []
        for p in sorted(cards_dir.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
            try:
                cards.append(cls.load(p))
            except Exception:
                pass
        return cards

    def touch(self, cards_dir: Path) -> None:
        self.updated = datetime.now().isoformat(timespec="seconds")
        self.save(cards_dir)


class Task(BaseModel):
    id:           str
    status:       TaskStatus
    account:      str
    type:         AccountType
    prompt:       str
    project:      str
    project_name: str = ""
    conversation_id:   str = ""
    native_session_id: str = ""
    pid:      str = ""
    created:  str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    finished: Optional[str] = None
    archived: bool = False
    auto:     bool = False
    images:   list[str] = Field(default_factory=list)
    execute_at: Optional[str] = None
    # DAG / workflow fields
    parent_task_id: str = ""
    depends_on:     list[str] = Field(default_factory=list)
    pipeline_id:    str = ""
    board_card_id:  str = ""
    # Token usage (populated after task completes)
    tokens_input:  int = 0
    tokens_output: int = 0
    cost_usd:      float = 0.0
    # Ephemeral task fields
    ephemeral:    bool = False
    execution_mode: str = "persistent"  # persistent | ephemeral
    output_dir:   str = ""
    ephemeral_retention: str = "release_on_finish"
    ephemeral_ttl_minutes: int = 120
    ephemeral_container_name: str = ""
    task_secrets: dict = Field(default_factory=dict)

    # ── 持久化 ────────────────────────────────────────────

    def save(self, tasks_dir: Path) -> None:
        tasks_dir.mkdir(parents=True, exist_ok=True)
        path = tasks_dir / f"{self.id}.json"
        path.write_text(
            json.dumps(self.model_dump(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: Path) -> "Task":
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(**data)

    @classmethod
    def load_all(cls, tasks_dir: Path) -> list["Task"]:
        if not tasks_dir.exists():
            return []
        tasks = []
        for p in sorted(tasks_dir.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
            try:
                tasks.append(cls.load(p))
            except Exception:
                pass
        return tasks

    def update_status(self, status: TaskStatus, tasks_dir: Path) -> None:
        self.status = status
        if status in (TaskStatus.done, TaskStatus.failed, TaskStatus.killed):
            self.finished = datetime.now().isoformat(timespec="seconds")
        self.save(tasks_dir)

    @property
    def log_path_str(self) -> str:
        return f"tasks/{self.id}.log"


# ── HTTP 请求/响应结构 ─────────────────────────────────────

class TaskCreateRequest(BaseModel):
    prompt:  str
    account: Optional[str] = None        # 指定账号名
    project: Optional[str] = None        # 按项目路径匹配
    project_name: Optional[str] = None   # 按项目配置名匹配
    type:    Optional[AccountType] = None
    auto:    bool = False                 # 全自动模式（--full-auto / --dangerously-skip-permissions）
    conversation_id:   Optional[str] = None
    conversation_name: Optional[str] = None
    images:  list[str] = []              # 容器内图片路径列表（--image 参数）
    execute_at: Optional[str] = None     # 定时执行时间
    # DAG / workflow fields
    parent_task_id: Optional[str] = None
    depends_on:     list[str] = []
    pipeline_id:    Optional[str] = None
    board_card_id:  Optional[str] = None
    # Ephemeral task fields
    ephemeral:  bool = False
    execution_mode: Optional[str] = None
    secrets:    dict = {}
    output_dir: str = ""
    ephemeral_retention: Optional[str] = None
    ephemeral_ttl_minutes: Optional[int] = None


class TaskResponse(BaseModel):
    id:           str
    status:       TaskStatus
    account:      str
    type:         AccountType
    prompt:       str
    project:      str
    project_name: str = ""
    conversation_id: str = ""
    native_session_id: str = ""
    created: str
    finished: Optional[str] = None
    log_path: str
    archived: bool = False
    auto:     bool = False
    images:   list[str] = []
    execute_at: Optional[str] = None
    parent_task_id: str = ""
    depends_on:     list[str] = []
    pipeline_id:    str = ""
    board_card_id:  str = ""
    tokens_input:  int = 0
    tokens_output: int = 0
    cost_usd:      float = 0.0
    ephemeral:    bool = False
    execution_mode: str = "persistent"
    output_dir:   str = ""
    ephemeral_retention: str = "release_on_finish"
    ephemeral_ttl_minutes: int = 120
    ephemeral_container_name: str = ""

    @classmethod
    def from_task(cls, t: Task) -> "TaskResponse":
        return cls(
            id           = t.id,
            status       = t.status,
            account      = t.account,
            type         = t.type,
            prompt       = t.prompt,
            project      = t.project,
            project_name = t.project_name,
            conversation_id   = t.conversation_id,
            native_session_id = t.native_session_id,
            created      = t.created,
            finished     = t.finished,
            log_path     = t.log_path_str,
            archived     = t.archived,
            auto         = getattr(t, "auto", False),
            images       = getattr(t, "images", []),
            execute_at   = getattr(t, "execute_at", None),
            parent_task_id = getattr(t, "parent_task_id", ""),
            depends_on     = getattr(t, "depends_on", []),
            pipeline_id    = getattr(t, "pipeline_id", ""),
            board_card_id  = getattr(t, "board_card_id", ""),
            tokens_input   = getattr(t, "tokens_input", 0),
            tokens_output  = getattr(t, "tokens_output", 0),
            cost_usd       = getattr(t, "cost_usd", 0.0),
            ephemeral      = getattr(t, "ephemeral", False),
            execution_mode = getattr(t, "execution_mode", "ephemeral" if getattr(t, "ephemeral", False) else "persistent"),
            output_dir     = getattr(t, "output_dir", ""),
            ephemeral_retention = getattr(t, "ephemeral_retention", "release_on_finish"),
            ephemeral_ttl_minutes = getattr(t, "ephemeral_ttl_minutes", 120),
            ephemeral_container_name = getattr(t, "ephemeral_container_name", ""),
        )


class BoardResponse(BaseModel):
    id:      str
    name:    str
    project_name: str = ""
    created: str
    updated: str

    @classmethod
    def from_board(cls, b: Board) -> "BoardResponse":
        return cls(
            id=b.id,
            name=b.name,
            project_name=b.project_name,
            created=b.created,
            updated=b.updated,
        )


class BoardCardResponse(BaseModel):
    id:              str
    board_id:        str
    title:           str
    description:     str = ""
    project_name:    str = ""
    status:          BoardCardStatus
    priority:        str = "normal"
    conversation_id: str = ""
    task_ids:        list[str] = []
    pipeline_id:     str = ""
    archived:        bool = False
    created:         str
    updated:         str

    @classmethod
    def from_card(cls, c: BoardCard) -> "BoardCardResponse":
        return cls(**c.model_dump())


class ConversationResponse(BaseModel):
    id:                str
    name:              str
    account:           str
    type:              AccountType
    project:           str
    project_name:      str = ""
    native_session_id: str
    status:            ConversationStatus
    mode:              ConversationMode = ConversationMode.chat
    tmux_session:      str = ""
    created:           str
    updated:           str
    last_task_id:      str
    ephemeral:         bool = False
    output_dir:        str = ""
    ephemeral_retention: str = "release_on_finish"
    ephemeral_ttl_minutes: int = 120
    ephemeral_container_name: str = ""
    ephemeral_expires_at: str = ""

    @classmethod
    def from_conversation(cls, c: Conversation) -> "ConversationResponse":
        return cls(
            id                = c.id,
            name              = c.name,
            account           = c.account,
            type              = c.type,
            project           = c.project,
            project_name      = c.project_name,
            native_session_id = c.native_session_id,
            status            = c.status,
            mode              = c.mode,
            tmux_session      = c.tmux_session,
            created           = c.created,
            updated           = c.updated,
            last_task_id      = c.last_task_id,
            ephemeral         = getattr(c, "ephemeral", False),
            output_dir        = getattr(c, "output_dir", ""),
            ephemeral_retention = getattr(c, "ephemeral_retention", "release_on_finish"),
            ephemeral_ttl_minutes = getattr(c, "ephemeral_ttl_minutes", 120),
            ephemeral_container_name = getattr(c, "ephemeral_container_name", ""),
            ephemeral_expires_at = getattr(c, "ephemeral_expires_at", ""),
        )


class ProjectResponse(BaseModel):
    name:        str
    account:     str
    path:        str
    active:      bool = True
    ide_enabled: bool = False
    ide_port:    Optional[int] = None
    ide_auth:    str = "none"
    ide_remote:  bool = False
    ide_url:     str = ""
    image:       str = ""
    ephemeral:   bool = False
    git_url:     str = ""

    @classmethod
    def from_project(cls, p: Project) -> "ProjectResponse":
        ide_url = f"http://127.0.0.1:{p.ide_port}" if p.ide_enabled and p.ide_port else ""
        return cls(
            name=p.name,
            account=p.account,
            path=p.path,
            active=p.active,
            ide_enabled=p.ide_enabled,
            ide_port=p.ide_port,
            ide_auth=p.ide_auth,
            ide_remote=p.ide_remote,
            ide_url=ide_url,
            image=p.image,
            ephemeral=getattr(p, "ephemeral", False),
            git_url=getattr(p, "git_url", ""),
        )


class AccountResponse(BaseModel):
    name:      str
    type:      AccountType
    auth:      AccountAuth
    env_file:  str = ""
    proxy:     AccountProxy
    projects:  list[str]
    running:   bool         # 容器是否在线
    busy:      bool         # 是否有 running 任务占用
    container: str
    running_task_id:     str = ""
    running_task_prompt: str = ""
    task_done_count:     int = 0
    task_failed_count:   int = 0


# ── 工作流 / Pipeline ──────────────────────────────────────

class PipelineNodeRun(BaseModel):
    node_id:          str
    node_name:        str = ""
    task_id:          str = ""
    target_mode:      str = "default"
    project_name:     str = ""
    project_role:     str = ""
    account:          str = ""
    resolved_project: str = ""
    actual_prompt:    str = ""
    execution_mode:   str = "inherit"
    output_dir:       str = ""
    ephemeral_retention: str = "release_on_finish"
    ephemeral_ttl_minutes: int = 120


class Pipeline(BaseModel):
    id:              str
    name:            str
    project_name:    str = ""
    task_ids:        list[str] = Field(default_factory=list)
    node_runs:       list[PipelineNodeRun] = Field(default_factory=list)
    template_id:     str = ""
    trigger_input:   str = ""
    status:          str = "running"  # running | succeeded | failed | partial
    last_error:      str = ""        # last unhandled exception message (if any)
    project_map:     dict[str, str] = Field(default_factory=dict)
    default_project: str = ""
    default_account: str = ""
    workspace_policy: str = "isolated"  # isolated | shared_ephemeral | artifact_sync
    shared_conversation_id: str = ""
    artifact_dir: str = ""
    created:         str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    updated:         str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    def save(self, pipelines_dir: Path) -> None:
        pipelines_dir.mkdir(parents=True, exist_ok=True)
        path = pipelines_dir / f"{self.id}.json"
        path.write_text(
            json.dumps(self.model_dump(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: Path) -> "Pipeline":
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(**data)

    @classmethod
    def load_all(cls, pipelines_dir: Path) -> list["Pipeline"]:
        if not pipelines_dir.exists():
            return []
        pipelines = []
        for p in sorted(pipelines_dir.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
            try:
                pipelines.append(cls.load(p))
            except Exception:
                pass
        return pipelines

    def touch(self, pipelines_dir: Path) -> None:
        self.updated = datetime.now().isoformat(timespec="seconds")
        self.save(pipelines_dir)


class PipelineResponse(BaseModel):
    id:              str
    name:            str
    project_name:    str = ""
    task_ids:        list[str] = []
    node_runs:       list[PipelineNodeRun] = []
    template_id:     str = ""
    trigger_input:   str = ""
    status:          str = "running"
    last_error:      str = ""
    project_map:     dict[str, str] = {}
    default_project: str = ""
    default_account: str = ""
    workspace_policy: str = "isolated"
    shared_conversation_id: str = ""
    artifact_dir: str = ""
    created:         str
    updated:         str
    tasks:           list[TaskResponse] = []

    @classmethod
    def from_pipeline(cls, p: Pipeline, tasks: list[Task] = []) -> "PipelineResponse":
        return cls(
            id              = p.id,
            name            = p.name,
            project_name    = p.project_name,
            task_ids        = p.task_ids,
            node_runs       = getattr(p, "node_runs", []),
            template_id     = p.template_id,
            trigger_input   = p.trigger_input,
            status          = getattr(p, "status", "running"),
            last_error      = getattr(p, "last_error", ""),
            project_map     = getattr(p, "project_map", {}),
            default_project = getattr(p, "default_project", ""),
            default_account = getattr(p, "default_account", ""),
            workspace_policy = getattr(p, "workspace_policy", "isolated"),
            shared_conversation_id = getattr(p, "shared_conversation_id", ""),
            artifact_dir = getattr(p, "artifact_dir", ""),
            created         = p.created,
            updated         = p.updated,
            tasks           = [TaskResponse.from_task(t) for t in tasks],
        )


class LogicalProjectEntry(BaseModel):
    name:    str
    account: str
    type:    str


class LogicalProject(BaseModel):
    path:         str
    display_name: str
    projects:     list[LogicalProjectEntry]


# ── 工作流模板 ─────────────────────────────────────────────

class NodeType(str, Enum):
    task      = "task"       # 默认：提交 AI 任务
    condition = "condition"  # 条件路由节点（不提交任务，根据上游输出决定分支）
    delay     = "delay"      # 延迟等待节点
    approval  = "approval"   # 人工审批节点（等待用户手动批准后继续）


class ConditionBranch(BaseModel):
    """条件节点的一个分支：condition 为真时激活 next_nodes 中的下游节点。"""
    condition:  str             # 表达式，支持格式：
                                #   "{{steps.X.outputs.text}} contains 'ERROR'"
                                #   "{{steps.X.outputs.text}} not_contains 'ok'"
                                #   "{{steps.X.outputs.text}} equals 'done'"
                                #   "else"（fallback 分支，总为真）
    next_nodes: list[str] = Field(default_factory=list)  # 激活的下游 node_id 列表


class TemplateNode(BaseModel):
    """模板中的一个节点定义（可重复使用的任务蓝图）"""
    node_id:              str
    name:                 str
    prompt_tpl:           str = ""         # 支持 {{input}} 和 {{steps.<id>.outputs.text}}
    target_mode:          str = "default"  # default / fixed_project / runtime_role
    project_name:         str = ""         # target_mode=fixed_project 时使用
    project_role:         str = ""         # 角色名（如 "claude"/"codex"）或具体项目名
    account:              str = ""         # execution_mode=ephemeral 且不指定项目时使用
    execution_mode:       str = "inherit"  # inherit / persistent / ephemeral
    output_dir:           str = ""
    ephemeral_retention:  str = "release_on_finish"
    ephemeral_ttl_minutes: int = 120
    depends_on:           list[str] = Field(default_factory=list)  # 依赖的 node_id 列表
    pos_x:                float = 0.0      # 画布坐标（Drawflow）
    pos_y:                float = 0.0
    max_retries:          int = 0          # 节点失败后最大重试次数（0=不重试）
    retry_delay_seconds:  int = 30         # 每次重试前等待秒数
    timeout_minutes:      int = 120        # 节点执行超时（分钟），0=不限制
    # 节点类型（新增）
    node_type:            NodeType = NodeType.task
    # condition 节点专用
    branches:             list[ConditionBranch] = Field(default_factory=list)
    # delay 节点专用
    delay_seconds:        int = 0


class WorkflowTemplate(BaseModel):
    id:          str
    name:        str
    description: str = ""
    nodes:       list[TemplateNode] = Field(default_factory=list)
    created:     str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    updated:     str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    def save(self, templates_dir: Path) -> None:
        templates_dir.mkdir(parents=True, exist_ok=True)
        path = templates_dir / f"{self.id}.json"
        path.write_text(
            json.dumps(self.model_dump(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: Path) -> "WorkflowTemplate":
        return cls(**json.loads(path.read_text(encoding="utf-8")))

    @classmethod
    def load_all(cls, templates_dir: Path) -> list["WorkflowTemplate"]:
        if not templates_dir.exists():
            return []
        result = []
        for p in sorted(templates_dir.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
            try:
                result.append(cls.load(p))
            except Exception:
                pass
        return result

    def touch(self, templates_dir: Path) -> None:
        self.updated = datetime.now().isoformat(timespec="seconds")
        self.save(templates_dir)


class WorkflowTemplateResponse(BaseModel):
    id:          str
    name:        str
    description: str
    nodes:       list[TemplateNode]
    created:     str
    updated:     str

    @classmethod
    def from_template(cls, t: WorkflowTemplate) -> "WorkflowTemplateResponse":
        return cls(
            id          = t.id,
            name        = t.name,
            description = t.description,
            nodes       = t.nodes,
            created     = t.created,
            updated     = t.updated,
        )


class TemplateCreateRequest(BaseModel):
    name:        str
    description: str = ""
    nodes:       list[TemplateNode] = []


class TemplateRunRequest(BaseModel):
    input:           str
    project_map:     dict[str, str] = {}  # project_role / node_id → 实际项目名
    default_project: str = ""
    default_account: str = ""
    workspace_policy: str = "isolated"


# ── Agent Skills ───────────────────────────────────────────

_FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)", re.DOTALL)


class Skill(BaseModel):
    slug:                     str
    name:                     str = ""
    description:              str = ""
    user_invocable:           bool = True
    disable_model_invocation: bool = False
    allowed_tools:            list[str] = Field(default_factory=list)
    content:                  str = ""   # markdown body after frontmatter

    @classmethod
    def from_content(cls, slug: str, text: str) -> "Skill":
        m = _FM_RE.match(text)
        if m:
            fm = yaml.safe_load(m.group(1)) or {}
            body = m.group(2).strip()
        else:
            fm, body = {}, text.strip()
        tools = fm.get("allowed-tools", [])
        if isinstance(tools, str):
            tools = [t.strip() for t in tools.split(",") if t.strip()]
        return cls(
            slug                     = slug,
            name                     = fm.get("name", slug),
            description              = fm.get("description", ""),
            user_invocable           = bool(fm.get("user-invocable", True)),
            disable_model_invocation = bool(fm.get("disable-model-invocation", False)),
            allowed_tools            = tools,
            content                  = body,
        )

    @classmethod
    def from_file(cls, slug: str, path: Path) -> "Skill":
        text = path.read_text(encoding="utf-8")
        m = _FM_RE.match(text)
        if m:
            fm = yaml.safe_load(m.group(1)) or {}
            body = m.group(2).strip()
        else:
            fm, body = {}, text.strip()
        tools = fm.get("allowed-tools", [])
        if isinstance(tools, str):
            tools = [t.strip() for t in tools.split(",") if t.strip()]
        return cls(
            slug                     = slug,
            name                     = fm.get("name", slug),
            description              = fm.get("description", ""),
            user_invocable           = bool(fm.get("user-invocable", True)),
            disable_model_invocation = bool(fm.get("disable-model-invocation", False)),
            allowed_tools            = tools,
            content                  = body,
        )

    def to_skill_md(self) -> str:
        fm: dict = {"name": self.name or self.slug, "description": self.description}
        if not self.user_invocable:
            fm["user-invocable"] = False
        if self.disable_model_invocation:
            fm["disable-model-invocation"] = True
        if self.allowed_tools:
            fm["allowed-tools"] = self.allowed_tools
        header = yaml.dump(fm, allow_unicode=True, default_flow_style=False).rstrip()
        return f"---\n{header}\n---\n\n{self.content}\n"

    def save(self, skills_dir: Path) -> None:
        skill_dir = skills_dir / self.slug
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(self.to_skill_md(), encoding="utf-8")

    @classmethod
    def load_all(cls, skills_dir: Path) -> list["Skill"]:
        if not skills_dir.exists():
            return []
        skills = []
        for p in sorted(skills_dir.iterdir()):
            if p.is_dir() and (p / "SKILL.md").exists():
                try:
                    skills.append(cls.from_file(p.name, p / "SKILL.md"))
                except Exception:
                    pass
        return skills


class SkillUpsertRequest(BaseModel):
    name:                     str = ""
    description:              str = ""
    user_invocable:           bool = True
    disable_model_invocation: bool = False
    allowed_tools:            list[str] = Field(default_factory=list)
    content:                  str = ""


class MarketplaceInstallRequest(BaseModel):
    plugin: dict   # full entry dict from MarketplaceManager.search()
    slug:   str    # target slug for the installed skill


# ── 定时计划 ───────────────────────────────────────────────

# ── 日报 ──────────────────────────────────────────────────

class DigestStatus(str, Enum):
    pending    = "pending"
    generating = "generating"
    ready      = "ready"
    error      = "error"


class ProjectDigestStats(BaseModel):
    project_name:  str
    done:          int = 0
    failed:        int = 0
    killed:        int = 0
    tokens_input:  int = 0
    tokens_output: int = 0
    cost_usd:      float = 0.0
    prompts:       list[str] = Field(default_factory=list)


class DailyDigest(BaseModel):
    date:          str
    status:        DigestStatus = DigestStatus.pending
    total_done:    int = 0
    total_failed:  int = 0
    total_killed:  int = 0
    tokens_input:  int = 0
    tokens_output: int = 0
    cost_usd:      float = 0.0
    projects:      list[ProjectDigestStats] = Field(default_factory=list)
    ai_summary:    str = ""
    ai_task_id:    str = ""
    generated_at:  str = ""
    created:       str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    updated:       str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    def save(self, digests_dir: Path) -> None:
        digests_dir.mkdir(parents=True, exist_ok=True)
        path = digests_dir / f"{self.date}.json"
        path.write_text(
            json.dumps(self.model_dump(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: Path) -> "DailyDigest":
        return cls(**json.loads(path.read_text(encoding="utf-8")))


# ── 定时计划 ───────────────────────────────────────────────

class ScheduleType(str, Enum):
    daily  = "daily"    # 每天 HH:MM
    weekly = "weekly"   # 每周指定星期 HH:MM
    hourly = "hourly"   # 每小时 :MM
    cron   = "cron"     # 自定义 cron 表达式


class Schedule(BaseModel):
    id:              str
    name:            str
    prompt:          str
    project_name:    str
    target_type:     str = "task"  # task | workflow
    template_id:     str = ""
    workflow_input:  str = ""
    project_map:     dict[str, str] = Field(default_factory=dict)
    default_project: str = ""
    default_account: str = ""
    workspace_policy: str = "isolated"
    schedule_type:   ScheduleType
    time_of_day:     Optional[str] = None    # "HH:MM" for daily/weekly
    days_of_week:    list[int] = Field(default_factory=list)  # 0=Mon..6=Sun
    minute_of_hour:  Optional[int] = None    # 0-59 for hourly
    cron_expr:       Optional[str] = None    # cron 表达式，schedule_type=cron 时使用
    enabled:         bool = True
    auto:            bool = False
    account:         Optional[str] = None
    execution_mode:  str = "inherit"  # inherit | persistent | ephemeral
    output_dir:      str = ""
    ephemeral_retention: str = "release_on_finish"
    ephemeral_ttl_minutes: int = 120
    webhook_token:   str = Field(default_factory=lambda: __import__("uuid").uuid4().hex)
    webhook_enabled: bool = False
    next_run_at:     Optional[str] = None
    last_run_at:     Optional[str] = None
    last_run_type:   str = ""
    last_task_id:    str = ""
    last_workflow_run_id: str = ""
    created:         str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    updated:         str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    def save(self, schedules_dir: Path) -> None:
        schedules_dir.mkdir(parents=True, exist_ok=True)
        path = schedules_dir / f"{self.id}.json"
        path.write_text(
            json.dumps(self.model_dump(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: Path) -> "Schedule":
        return cls(**json.loads(path.read_text(encoding="utf-8")))

    @classmethod
    def load_all(cls, schedules_dir: Path) -> list["Schedule"]:
        if not schedules_dir.exists():
            return []
        result = []
        for p in sorted(schedules_dir.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
            try:
                result.append(cls.load(p))
            except Exception:
                pass
        return result


class ScheduleResponse(BaseModel):
    id:              str
    name:            str
    prompt:          str
    project_name:    str
    target_type:     str = "task"
    template_id:     str = ""
    workflow_input:  str = ""
    project_map:     dict[str, str] = {}
    default_project: str = ""
    default_account: str = ""
    workspace_policy: str = "isolated"
    schedule_type:   ScheduleType
    time_of_day:     Optional[str] = None
    days_of_week:    list[int] = []
    minute_of_hour:  Optional[int] = None
    cron_expr:       Optional[str] = None
    enabled:         bool
    auto:            bool
    account:         Optional[str] = None
    execution_mode:  str = "inherit"
    output_dir:      str = ""
    ephemeral_retention: str = "release_on_finish"
    ephemeral_ttl_minutes: int = 120
    webhook_token:   str = ""
    webhook_enabled: bool = False
    next_run_at:     Optional[str] = None
    last_run_at:     Optional[str] = None
    last_run_type:   str = ""
    last_task_id:    str = ""
    last_workflow_run_id: str = ""
    created:         str
    updated:         str

    @classmethod
    def from_schedule(cls, s: "Schedule") -> "ScheduleResponse":
        return cls(**s.model_dump())


class ScheduleRunResponse(BaseModel):
    run_type: str
    schedule: Optional[ScheduleResponse] = None
    task: Optional[TaskResponse] = None
    workflow_run: Optional["WorkflowRunResponse"] = None


class TerminalConversationCreateRequest(BaseModel):
    name:         str
    project_name: str


class ScheduleCreateRequest(BaseModel):
    name:            str
    prompt:          str
    project_name:    str
    target_type:     str = "task"
    template_id:     str = ""
    workflow_input:  str = ""
    project_map:     dict[str, str] = {}
    default_project: str = ""
    default_account: str = ""
    workspace_policy: str = "isolated"
    schedule_type:   ScheduleType
    time_of_day:     Optional[str] = None
    days_of_week:    list[int] = []
    minute_of_hour:  Optional[int] = None
    cron_expr:       Optional[str] = None
    enabled:         bool = True
    auto:            bool = False
    account:         Optional[str] = None
    execution_mode:  str = "inherit"
    output_dir:      str = ""
    ephemeral_retention: str = "release_on_finish"
    ephemeral_ttl_minutes: int = 120
    webhook_enabled: bool = False


class ScheduleUpdateRequest(BaseModel):
    name:            Optional[str] = None
    prompt:          Optional[str] = None
    project_name:    Optional[str] = None
    target_type:     Optional[str] = None
    template_id:     Optional[str] = None
    workflow_input:  Optional[str] = None
    project_map:     Optional[dict[str, str]] = None
    default_project: Optional[str] = None
    default_account: Optional[str] = None
    workspace_policy: Optional[str] = None
    schedule_type:   Optional[ScheduleType] = None
    time_of_day:     Optional[str] = None
    days_of_week:    Optional[list[int]] = None
    minute_of_hour:  Optional[int] = None
    cron_expr:       Optional[str] = None
    enabled:         Optional[bool] = None
    auto:            Optional[bool] = None
    account:         Optional[str] = None
    execution_mode:  Optional[str] = None
    output_dir:      Optional[str] = None
    ephemeral_retention: Optional[str] = None
    ephemeral_ttl_minutes: Optional[int] = None
    webhook_enabled: Optional[bool] = None


# ══════════════════════════════════════════════════════════════
#  工作流 v2 模型（完整修改计划 - Phase 1 引入）
#  目标：引入真正的一等公民 WorkflowRun + 节点状态机
#  原则：完全 additive，旧 Pipeline / WorkflowTemplate / TemplateNode 保持 100% 不变
#  迁移策略（默认）：惰性按需升级（读取旧数据时自动转换为新结构 shim）
# ══════════════════════════════════════════════════════════════

class WorkflowNodeState(str, Enum):
    """工作流节点执行状态机"""
    pending          = "pending"
    waiting_deps     = "waiting_deps"
    running          = "running"
    succeeded        = "succeeded"
    failed           = "failed"
    skipped          = "skipped"          # 未命中条件分支
    cancelled        = "cancelled"
    waiting_approval = "waiting_approval" # 人工审批节点等待中


class NodeExecution(BaseModel):
    """单次 WorkflowRun 中一个节点的执行记录（带独立状态与尝试历史）"""
    node_id:           str
    name:              str = ""
    state:             WorkflowNodeState = WorkflowNodeState.pending
    depends_on:        list[str] = Field(default_factory=list)
    target_mode:       str = "default"
    project_name:      str = ""
    project_role:      str = ""
    account:           str = ""
    execution_mode:    str = "inherit"
    output_dir:        str = ""
    ephemeral_retention: str = "release_on_finish"
    ephemeral_ttl_minutes: int = 120
    task_id:           str = ""           # 当前（或最后一次）关联的 Task id
    attempt_count:     int = 0
    resolved_project:  str = ""
    actual_prompt:     str = ""
    outputs:           dict = Field(default_factory=dict)  # 未来 Phase 3 用于 {{steps.*.outputs}}
    started_at:        str = ""
    finished_at:       str = ""
    error_message:     str = ""
    # 历史尝试（重试时追加，当前 task_id 总是最后一次）
    attempts:          list[dict] = Field(default_factory=list)


class WorkflowRun(BaseModel):
    """
    工作流的一次执行实例（新核心实体，逐步替代 Pipeline 的“运行记录”语义）。
    每次从模板 run 产生一个 WorkflowRun，拥有完整节点状态机。
    """
    id:                          str
    template_id:                 str = ""
    template_version:            int = 1
    name:                        str = ""
    trigger_input:               str = ""
    status:                      str = "running"  # running | succeeded | failed | cancelled | partial | waiting_approval
    node_executions:             list[NodeExecution] = Field(default_factory=list)
    created:                     str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    updated:                     str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    # 兼容/审计
    legacy_pipeline_id:          str = ""         # 若由旧 Pipeline 迁移而来
    project_map:                 dict[str, str] = Field(default_factory=dict)  # 运行时角色->项目快照
    default_account:             str = ""
    workspace_policy:            str = "isolated"
    shared_conversation_id:      str = ""
    artifact_dir:                str = ""
    # 人工审批节点支持
    pending_approval_node_id:    str = ""
    approval_token:              str = ""

    def save(self, runs_dir: Path) -> None:
        runs_dir.mkdir(parents=True, exist_ok=True)
        path = runs_dir / f"{self.id}.json"
        path.write_text(
            json.dumps(self.model_dump(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: Path) -> "WorkflowRun":
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(**data)

    @classmethod
    def load_all(cls, runs_dir: Path) -> list["WorkflowRun"]:
        if not runs_dir.exists():
            return []
        runs = []
        for p in sorted(runs_dir.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
            try:
                runs.append(cls.load(p))
            except Exception:
                pass
        return runs

    def touch(self, runs_dir: Path) -> None:
        self.updated = datetime.now().isoformat(timespec="seconds")
        self.save(runs_dir)


class WorkflowRunResponse(BaseModel):
    id:                          str
    template_id:                 str = ""
    template_version:            int = 1
    name:                        str = ""
    trigger_input:               str = ""
    status:                      str = "running"
    node_executions:             list[NodeExecution] = []
    created:                     str
    updated:                     str
    legacy_pipeline_id:          str = ""
    project_map:                 dict[str, str] = {}
    default_project:             str = ""
    default_account:             str = ""
    workspace_policy:            str = "isolated"
    shared_conversation_id:      str = ""
    artifact_dir:                str = ""
    last_error:                  str = ""
    tasks:                       list[TaskResponse] = []
    pending_approval_node_id:    str = ""
    approval_token:              str = ""

    # Compatibility for the current DAG renderer while the frontend moves from
    # Pipeline-shaped data to WorkflowRun-shaped data.
    task_ids:           list[str] = []
    node_runs:          list[PipelineNodeRun] = []

    @classmethod
    def from_run(
        cls,
        run: WorkflowRun,
        tasks: list[Task] = [],
        default_project: str = "",
        last_error: str = "",
    ) -> "WorkflowRunResponse":
        task_ids = [n.task_id for n in run.node_executions if n.task_id]
        node_runs = [
            PipelineNodeRun(
                node_id          = n.node_id,
                node_name        = n.name,
                task_id          = n.task_id,
                target_mode      = n.target_mode,
                project_name     = n.project_name,
                project_role     = n.project_role,
                account          = getattr(n, "account", ""),
                resolved_project = n.resolved_project,
                actual_prompt    = n.actual_prompt,
                execution_mode   = getattr(n, "execution_mode", "inherit"),
                output_dir       = getattr(n, "output_dir", ""),
                ephemeral_retention = getattr(n, "ephemeral_retention", "release_on_finish"),
                ephemeral_ttl_minutes = getattr(n, "ephemeral_ttl_minutes", 120),
            )
            for n in run.node_executions
        ]
        return cls(
            id                 = run.id,
            template_id        = run.template_id,
            template_version   = run.template_version,
            name               = run.name,
            trigger_input      = run.trigger_input,
            status             = run.status,
            node_executions    = run.node_executions,
            created                  = run.created,
            updated                  = run.updated,
            legacy_pipeline_id       = run.legacy_pipeline_id,
            project_map              = run.project_map,
            default_project          = default_project,
            default_account          = getattr(run, "default_account", ""),
            workspace_policy         = getattr(run, "workspace_policy", "isolated"),
            shared_conversation_id   = getattr(run, "shared_conversation_id", ""),
            artifact_dir             = getattr(run, "artifact_dir", ""),
            last_error               = last_error,
            tasks                    = [TaskResponse.from_task(t) for t in tasks],
            task_ids                 = task_ids,
            node_runs                = node_runs,
            pending_approval_node_id = getattr(run, "pending_approval_node_id", ""),
            approval_token           = getattr(run, "approval_token", ""),
        )
