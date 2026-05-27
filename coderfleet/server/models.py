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
    name:    str
    account: str
    path:    str

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
    created:           str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    updated:           str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    last_task_id:      str = ""

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
        )


class ConversationResponse(BaseModel):
    id:                str
    name:              str
    account:           str
    type:              AccountType
    project:           str
    project_name:      str = ""
    native_session_id: str
    status:            ConversationStatus
    created:           str
    updated:           str
    last_task_id:      str

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
            created           = c.created,
            updated           = c.updated,
            last_task_id      = c.last_task_id,
        )


class ProjectResponse(BaseModel):
    name:    str
    account: str
    path:    str

    @classmethod
    def from_project(cls, p: Project) -> "ProjectResponse":
        return cls(name=p.name, account=p.account, path=p.path)


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
    resolved_project: str = ""
    actual_prompt:    str = ""


class Pipeline(BaseModel):
    id:            str
    name:          str
    project_name:  str = ""
    task_ids:      list[str] = Field(default_factory=list)
    node_runs:     list[PipelineNodeRun] = Field(default_factory=list)
    template_id:   str = ""   # 如果是从模板触发的，记录模板 ID
    trigger_input: str = ""   # 触发时的原始输入
    created:       str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    updated:       str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

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
    id:            str
    name:          str
    project_name:  str = ""
    task_ids:      list[str] = []
    node_runs:     list[PipelineNodeRun] = []
    template_id:   str = ""
    trigger_input: str = ""
    created:       str
    updated:       str
    tasks:         list[TaskResponse] = []

    @classmethod
    def from_pipeline(cls, p: Pipeline, tasks: list[Task] = []) -> "PipelineResponse":
        return cls(
            id            = p.id,
            name          = p.name,
            project_name  = p.project_name,
            task_ids      = p.task_ids,
            node_runs     = getattr(p, "node_runs", []),
            template_id   = p.template_id,
            trigger_input = p.trigger_input,
            created       = p.created,
            updated       = p.updated,
            tasks         = [TaskResponse.from_task(t) for t in tasks],
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

class TemplateNode(BaseModel):
    """模板中的一个节点定义（可重复使用的任务蓝图）"""
    node_id:      str
    name:         str
    prompt_tpl:   str              # 支持 {{input}} 变量替换
    target_mode:  str = "default"  # default / fixed_project / runtime_role
    project_name: str = ""         # target_mode=fixed_project 时使用
    project_role: str = ""         # 角色名（如 "claude"/"codex"）或具体项目名
    depends_on:   list[str] = Field(default_factory=list)  # 依赖的 node_id 列表
    pos_x:        float = 0.0      # 画布坐标（Drawflow）
    pos_y:        float = 0.0


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
