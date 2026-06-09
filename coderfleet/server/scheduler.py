"""
scheduler.py — 任务调度核心

职责：
- 解析 accounts.conf，获取账号列表
- 判断账号空闲/忙碌状态
- 分配任务到合适账号
- 异步执行任务，维护任务生命周期
"""
from __future__ import annotations

import asyncio
import json
import os
import random
import re
import shlex
import subprocess
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from coderfleet.server import docker_mgr
from coderfleet.account_type_registry import env_auth_type_ids as _env_auth_ids
from coderfleet.ports import allocate_ide_port
from coderfleet.server.models import (
    Account,
    AccountAuth,
    AccountProxy,
    AccountResponse,
    AccountType,
    Board,
    BoardCard,
    BoardCardStatus,
    Conversation,
    ConversationStatus,
    LogicalProject,
    LogicalProjectEntry,
    Pipeline,
    PipelineNodeRun,
    Project,
    Schedule,
    ScheduleType,
    Task,
    TaskStatus,
    TemplateNode,
    WorkflowTemplate,
)


def _truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _parse_optional_int(value: str) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class Scheduler:
    def __init__(self, workspace_dir: Path):
        self.workspace_dir  = workspace_dir
        self.accounts_conf  = workspace_dir / "accounts.conf"
        self.projects_conf  = workspace_dir / "projects.conf"
        self.tasks_dir      = workspace_dir / "tasks"
        self.conversations_dir = workspace_dir / "conversations"
        self.boards_dir     = workspace_dir / "boards"
        self.board_cards_dir = workspace_dir / "board_cards"
        self.pipelines_dir  = workspace_dir / "pipelines"
        self.templates_dir  = workspace_dir / "workflow_templates"
        self.workflow_runs_dir = workspace_dir / "workflow_runs"   # v2 工作流执行记录（完整修改计划）
        self.schedules_dir  = workspace_dir / "schedules"
        # task_id → asyncio.Task（后台运行的协程）
        self.digests_dir    = workspace_dir / "digests"
        self._running: dict[str, asyncio.Task] = {}
        self._loop_task: Optional[asyncio.Task] = None
        self._push_manager = None  # set by main.py after both are initialized
        self.workflow_engine = None  # Phase 1 后由 main.py 注入 WorkflowEngine(self.workspace_dir, self)
        self._last_auto_digest_date: str = ""

    async def _notify(self, task: Task) -> None:
        if self._push_manager is None:
            return
        status_map = {
            TaskStatus.done:   ("✅ 任务完成",   task.prompt),
            TaskStatus.failed: ("❌ 任务失败",   task.prompt),
            TaskStatus.killed: ("⚠️ 任务已终止", task.prompt),
        }
        entry = status_map.get(task.status)
        if entry is None:
            return
        title, prompt = entry
        body = (prompt[:70] + "…") if len(prompt) > 70 else prompt
        await self._push_manager.send_all(title, body)

    @staticmethod
    def task_process_marker(task_id: str) -> str:
        return f"coderfleet-task-{task_id}"

    @staticmethod
    def build_cli_command(
        acc_type: AccountType,
        prompt: str,
        auto: bool,
        task_id: str,
        native_session_id: str = "",
        container_workdir: str = "",
        images: list[str] | None = None,
    ) -> str:
        """
        构建在容器内执行的 CLI 命令。
        per-type 逻辑委托给注册表中的 build_inner_cmd，此处只处理通用包装。
        """
        from coderfleet.account_type_registry import get_spec
        spec     = get_spec(acc_type.value)
        marker   = shlex.quote(Scheduler.task_process_marker(task_id))
        task_env = shlex.quote(task_id)

        inner_cmd = spec.build_inner_cmd(
            prompt, auto, task_id, marker, task_env,
            native_session_id, list(images or []),
        )

        if container_workdir:
            inner_cmd = f"cd {shlex.quote(container_workdir)} && {inner_cmd}"

        task_log  = f"/workspace/.coderfleet-tasks/{task_id}.log"
        task_exit = f"/workspace/.coderfleet-tasks/{task_id}.exit"
        # 用子 shell ( ... ) 包裹 inner_cmd：exec -a 替换的是子 shell 进程，
        # 外层 bash 在子 shell 退出后仍可执行 echo $? 写入 exit 文件。
        wrapper_body = (
            f"( {inner_cmd} ) >> {shlex.quote(task_log)} 2>&1"
            f"; echo $? > {shlex.quote(task_exit)}"
        )
        return (
            f"mkdir -p /workspace/.coderfleet-tasks"
            f" && setsid bash -c {shlex.quote(wrapper_body)} &"
        )

    @staticmethod
    def build_usage_status_command(acc_type: AccountType) -> str:
        from coderfleet.account_type_registry import get_spec
        try:
            return get_spec(acc_type.value).usage_status_cmd
        except KeyError:
            return ""

    @staticmethod
    def extract_native_session_id(acc_type: AccountType, text: str) -> str:
        from coderfleet.account_type_registry import get_spec
        try:
            return get_spec(acc_type.value).extract_session_id(text)
        except KeyError:
            return ""

    # ── 账号管理 ──────────────────────────────────────────

    def get_accounts(self) -> list[Account]:
        """解析 accounts.conf，返回所有账号"""
        accounts = []
        if not self.accounts_conf.exists():
            return accounts
        for line in self.accounts_conf.read_text(encoding="utf-8").splitlines():
            line = line.strip().rstrip("\r")
            if not line or line.startswith("#"):
                continue
            parts = {}
            for token in line.split():
                if "=" in token:
                    k, v = token.split("=", 1)
                    parts[k.upper()] = v
            if "NAME" not in parts or "TYPE" not in parts:
                continue
            try:
                acc_type = AccountType(parts["TYPE"])
                auth = AccountAuth(parts.get("AUTH", AccountAuth.login.value))
                proxy = AccountProxy(parts.get("PROXY", AccountProxy.relay.value))
            except ValueError:
                continue
            env_file = parts.get("ENV_FILE", "")
            if auth == AccountAuth.env and acc_type.value not in _env_auth_ids():
                continue
            if auth == AccountAuth.env and not env_file:
                env_file = f"./accounts/{parts['NAME']}/env"
            accounts.append(Account(
                name     = parts["NAME"],
                type     = acc_type,
                auth     = auth,
                env_file = env_file,
                proxy    = proxy,
            ))
        return accounts

    def get_projects(self) -> list[Project]:
        projects: list[Project] = []
        if self.projects_conf.exists():
            for line in self.projects_conf.read_text(encoding="utf-8").splitlines():
                line = line.strip().rstrip("\r")
                if not line or line.startswith("#"):
                    continue
                parts = {}
                for token in line.split():
                    if "=" in token:
                        k, v = token.split("=", 1)
                        parts[k.upper()] = v
                if "NAME" not in parts or "ACCOUNT" not in parts or "PATH" not in parts:
                    continue
                path = parts["PATH"].replace("~", str(Path.home()), 1)
                projects.append(Project(
                    name=parts["NAME"],
                    account=parts["ACCOUNT"],
                    path=path,
                    active=_truthy(parts.get("ACTIVE", "on")),
                    ide_enabled=_truthy(parts.get("IDE", "off")),
                    ide_port=_parse_optional_int(parts.get("IDE_PORT", "")),
                    ide_auth=parts.get("IDE_AUTH", "none"),
                    ide_remote=_truthy(parts.get("IDE_REMOTE", "off")),
                ))

        return projects

    # ── conf 文件写入 ──────────────────────────────────────

    def _rewrite_conf(self, conf_path: Path, key: str, new_line: Optional[str]) -> None:
        """原子重写 conf 文件：替换/追加/删除 NAME=key 的行，注释行保留。"""
        lines: list[str] = []
        if conf_path.exists():
            lines = conf_path.read_text(encoding="utf-8").splitlines(keepends=True)

        target_idx: Optional[int] = None
        for i, raw in enumerate(lines):
            stripped = raw.strip()
            if not stripped or stripped.startswith("#"):
                continue
            parts: dict[str, str] = {}
            for token in stripped.split():
                if "=" in token:
                    k, v = token.split("=", 1)
                    parts[k.upper()] = v
            if parts.get("NAME") == key:
                target_idx = i
                break

        if new_line is None:          # 删除
            if target_idx is not None:
                lines.pop(target_idx)
        else:
            entry = new_line.rstrip("\n") + "\n"
            if target_idx is not None:
                lines[target_idx] = entry
            else:
                lines.append(entry)

        conf_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = conf_path.with_suffix(".tmp")
        tmp.write_text("".join(lines), encoding="utf-8")
        tmp.replace(conf_path)

    def save_account(
        self,
        name:     str,
        acc_type: AccountType,
        auth:     AccountAuth,
        proxy:    AccountProxy,
    ) -> Account:
        """新增或修改 accounts.conf 中的账号行，env 认证时自动创建 env 文件占位。"""
        parts = [f"NAME={name}", f"TYPE={acc_type.value}", f"AUTH={auth.value}", f"PROXY={proxy.value}"]
        env_file = ""
        if auth == AccountAuth.env:
            env_file = f"./accounts/{name}/env"
            parts.append(f"ENV_FILE={env_file}")
            env_path = self.workspace_dir / "accounts" / name / "env"
            env_path.parent.mkdir(parents=True, exist_ok=True)
            if not env_path.exists():
                env_path.write_text("", encoding="utf-8")
                try:
                    env_path.chmod(0o600)
                except OSError:
                    pass
        self._rewrite_conf(self.accounts_conf, name, " ".join(parts))
        return Account(name=name, type=acc_type, auth=auth, proxy=proxy, env_file=env_file)

    def delete_account(self, name: str) -> None:
        """从 accounts.conf 删除账号行。"""
        self._rewrite_conf(self.accounts_conf, name, None)

    def _resolve_env_path(self, acc: Account) -> Path:
        env_file = acc.env_file or f"./accounts/{acc.name}/env"
        p = Path(env_file)
        return p if p.is_absolute() else self.workspace_dir / p

    def get_account_env(self, name: str) -> dict[str, str]:
        """读取账号 env 文件，返回变量字典（原始值，由调用方决定是否脱敏）。"""
        acc = next((a for a in self.get_accounts() if a.name == name), None)
        if acc is None:
            raise ValueError(f"账号 '{name}' 不存在")
        env_path = self._resolve_env_path(acc)
        if not env_path.exists():
            return {}
        result: dict[str, str] = {}
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            result[k.strip()] = v.strip()
        return result

    def set_account_env(self, name: str, vars: dict[str, str]) -> None:
        """合并写入 env 文件；value 为空字符串表示删除该变量。"""
        acc = next((a for a in self.get_accounts() if a.name == name), None)
        if acc is None:
            raise ValueError(f"账号 '{name}' 不存在")
        env_path = self._resolve_env_path(acc)
        env_path.parent.mkdir(parents=True, exist_ok=True)
        existing: dict[str, str] = {}
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                existing[k.strip()] = v.strip()
        for k, v in vars.items():
            if v == "":
                existing.pop(k, None)
            else:
                existing[k] = v
        env_path.write_text("".join(f"{k}={v}\n" for k, v in existing.items()), encoding="utf-8")
        try:
            env_path.chmod(0o600)
        except OSError:
            pass

    def _project_env_path(self, name: str) -> Path:
        return self.workspace_dir / "projects" / name / "env"

    def get_project_env(self, name: str) -> dict[str, str]:
        """读取项目 env 文件，返回变量字典。"""
        if not any(p.name == name for p in self.get_projects()):
            raise ValueError(f"项目 '{name}' 不存在")
        env_path = self._project_env_path(name)
        if not env_path.exists():
            return {}
        result: dict[str, str] = {}
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            result[k.strip()] = v.strip()
        return result

    def set_project_env(self, name: str, vars: dict[str, str]) -> None:
        """合并写入项目 env 文件；value 为空字符串表示删除该变量。"""
        if not any(p.name == name for p in self.get_projects()):
            raise ValueError(f"项目 '{name}' 不存在")
        env_path = self._project_env_path(name)
        env_path.parent.mkdir(parents=True, exist_ok=True)
        existing: dict[str, str] = {}
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                existing[k.strip()] = v.strip()
        for k, v in vars.items():
            if v == "":
                existing.pop(k, None)
            else:
                existing[k] = v
        env_path.write_text("".join(f"{k}={v}\n" for k, v in existing.items()), encoding="utf-8")
        try:
            env_path.chmod(0o600)
        except OSError:
            pass

    def save_project(
        self,
        name: str,
        account: str,
        path: str,
        active: bool = True,
        ide_enabled: bool = False,
        ide_port: Optional[int] = None,
        ide_auth: str = "none",
        ide_remote: bool = False,
    ) -> Project:
        """新增或修改 projects.conf 中的项目行。"""
        path_norm = str(Path(path).expanduser())
        if not ide_enabled:
            ide_port = None
            ide_auth = "none"
            ide_remote = False
        elif ide_port is None:
            used_ports = [
                p.ide_port
                for p in self.get_projects()
                if p.name != name
            ]
            ide_port = allocate_ide_port(used_ports)
        parts = [f"NAME={name}", f"ACCOUNT={account}", f"PATH={path_norm}"]
        if not active:
            parts.append("ACTIVE=off")
        if ide_enabled:
            parts.append("IDE=on")
            if ide_port is not None:
                parts.append(f"IDE_PORT={ide_port}")
            if ide_auth and ide_auth != "none":
                parts.append(f"IDE_AUTH={ide_auth}")
            if ide_remote:
                parts.append("IDE_REMOTE=on")
        self._rewrite_conf(self.projects_conf, name, " ".join(parts))
        return Project(
            name=name,
            account=account,
            path=path_norm,
            active=active,
            ide_enabled=ide_enabled,
            ide_port=ide_port,
            ide_auth=ide_auth,
            ide_remote=ide_remote,
        )

    def delete_project(self, name: str) -> None:
        """从 projects.conf 删除项目行。"""
        self._rewrite_conf(self.projects_conf, name, None)

    def get_busy_accounts(self) -> set[str]:
        """返回当前有 running 任务的账号名集合"""
        busy = set()
        for task in Task.load_all(self.tasks_dir):
            if task.status == TaskStatus.running:
                busy.add(task.account)
        return busy

    def list_accounts(self) -> list[AccountResponse]:
        # Single pass: collect running tasks + done/failed counts per account
        running_tasks: dict[str, Task] = {}
        done_counts:   dict[str, int]  = {}
        failed_counts: dict[str, int]  = {}
        for task in Task.load_all(self.tasks_dir):
            if task.status == TaskStatus.running:
                if task.account not in running_tasks:
                    running_tasks[task.account] = task
            elif task.status == TaskStatus.done:
                done_counts[task.account] = done_counts.get(task.account, 0) + 1
            elif task.status == TaskStatus.failed:
                failed_counts[task.account] = failed_counts.get(task.account, 0) + 1
        busy = set(running_tasks.keys())

        result = []
        projects_by_account: dict[str, list[str]] = {}
        for project in self.get_projects():
            projects_by_account.setdefault(project.account, []).append(project.name)
        for acc in self.get_accounts():
            project_names = projects_by_account.get(acc.name, [])
            containers = []
            running = False
            for pn in project_names:
                ctr = f"{acc.type.value}-{pn}"
                containers.append(ctr)
                if docker_mgr.is_container_running(ctr):
                    running = True
            rt = running_tasks.get(acc.name)
            result.append(AccountResponse(
                name      = acc.name,
                type      = acc.type,
                auth      = acc.auth,
                env_file  = acc.env_file,
                proxy     = acc.proxy,
                projects  = project_names,
                running   = running,
                busy      = acc.name in busy,
                container = " ".join(containers),
                running_task_id     = rt.id     if rt else "",
                running_task_prompt = rt.prompt if rt else "",
                task_done_count     = done_counts.get(acc.name, 0),
                task_failed_count   = failed_counts.get(acc.name, 0),
            ))
        return result

    def list_projects(self) -> list[Project]:
        return self.get_projects()

    def find_idle_account(
        self,
        prefer_type:    Optional[AccountType] = None,
        prefer_project: Optional[str]         = None,
    ) -> Optional[Account]:
        """
        找一个满足条件的可用账号：
        - 类型匹配（可选）
        - 项目路径匹配（可选，规范化后比较）
        - 至少有一个项目容器在线
        同一账号可以并发执行多个任务（不同 conversation），不再过滤 busy 账号。
        """
        for acc in self.get_accounts():
            if prefer_type and acc.type != prefer_type:
                continue
            if prefer_project:
                project_match = self.find_project_for_path(prefer_project, acc.name)
                if not project_match:
                    continue
            account_projects = [p for p in self.get_projects() if p.account == acc.name]
            if not account_projects:
                continue
            any_running = any(
                docker_mgr.is_container_running(p.container_name(acc.type))
                for p in account_projects
            )
            if not any_running:
                continue
            return acc

        return None

    @staticmethod
    def _canonical_path(path: str) -> Path:
        return Path(path).expanduser().resolve()

    def _path_under_root(self, root: str, project: str) -> bool:
        account_root = self._canonical_path(root)
        project_path = self._canonical_path(project)
        try:
            project_path.relative_to(account_root)
        except ValueError:
            return False
        return True

    def account_can_access_project(self, acc: Account, project: str) -> bool:
        return any(
            p.account == acc.name and self._path_under_root(p.path, project)
            for p in self.get_projects()
        )

    def find_project_by_name(self, name: str) -> Optional[Project]:
        return next((p for p in self.get_projects() if p.name == name), None)

    def find_project_for_path(self, project: Optional[str], account: Optional[str] = None) -> Optional[Project]:
        if not project:
            return None
        matching = [
            p for p in self.get_projects()
            if self._path_under_root(p.path, project)
        ]
        if account:
            matching = [p for p in matching if p.account == account]
        if not matching:
            return None
        return max(matching, key=lambda p: len(str(self._canonical_path(p.path))))

    def resolve_task_project(self, acc: Account, project: Optional[str]) -> str:
        if not project:
            account_projects = [p for p in self.get_projects() if p.account == acc.name]
            if len(account_projects) == 1:
                return str(self._canonical_path(account_projects[0].path))
            if not account_projects:
                raise ValueError(f"账号 '{acc.name}' 未关联项目，请先创建项目")
            raise ValueError(f"账号 '{acc.name}' 关联了多个项目，请选择项目")
        if not self.account_can_access_project(acc, project):
            raise ValueError(f"项目 '{project}' 未关联账号 '{acc.name}'")
        return str(self._canonical_path(project))

    def container_workdir_for_project(self, owner: Project, project: str) -> str:
        account_root = self._canonical_path(owner.path)
        project_path = self._canonical_path(project)
        rel = project_path.relative_to(account_root)
        if str(rel) == ".":
            return "/workspace"
        return "/workspace/" + rel.as_posix()

    # ── 任务管理 ──────────────────────────────────────────

    @staticmethod
    def new_task_id() -> str:
        ts   = datetime.now().strftime("%Y%m%d%H%M%S")
        rand = random.randint(0, 9999)
        return f"{ts}-{rand:04d}"

    def list_tasks(self) -> list[Task]:
        return Task.load_all(self.tasks_dir)

    def get_task(self, task_id: str) -> Optional[Task]:
        path = self.tasks_dir / f"{task_id}.json"
        if not path.exists():
            return None
        return Task.load(path)

    def get_log_path(self, task_id: str) -> Path:
        return self.tasks_dir / f"{task_id}.log"

    # ── 定时计划管理 ──────────────────────────────────────

    def new_schedule_id(self) -> str:
        ts = datetime.now().strftime("%Y%m%d%H%M%S")
        return f"sched-{ts}-{uuid.uuid4().hex[:4]}"

    def list_schedules(self) -> list[Schedule]:
        return Schedule.load_all(self.schedules_dir)

    def get_schedule(self, sched_id: str) -> Optional[Schedule]:
        p = self.schedules_dir / f"{sched_id}.json"
        if not p.exists():
            return None
        return Schedule.load(p)

    def create_schedule(self, sched: Schedule) -> Schedule:
        sched.next_run_at = self._compute_next_run_at(sched)
        sched.save(self.schedules_dir)
        return sched

    def update_schedule(self, sched_id: str, updates: dict) -> Optional[Schedule]:
        sched = self.get_schedule(sched_id)
        if sched is None:
            return None
        for k, v in updates.items():
            if hasattr(sched, k):
                setattr(sched, k, v)
        sched.updated = datetime.now().isoformat(timespec="seconds")
        sched.next_run_at = self._compute_next_run_at(sched)
        sched.save(self.schedules_dir)
        return sched

    def delete_schedule(self, sched_id: str) -> bool:
        p = self.schedules_dir / f"{sched_id}.json"
        if not p.exists():
            return False
        p.unlink()
        return True

    def _compute_next_run_at(self, sched: Schedule) -> Optional[str]:
        if not sched.enabled:
            return None
        now = datetime.now()

        if sched.schedule_type == ScheduleType.daily:
            if not sched.time_of_day:
                return None
            h, m = map(int, sched.time_of_day.split(":"))
            candidate = now.replace(hour=h, minute=m, second=0, microsecond=0)
            if candidate <= now:
                candidate += timedelta(days=1)
            return candidate.isoformat(timespec="seconds")

        elif sched.schedule_type == ScheduleType.weekly:
            if not sched.time_of_day or not sched.days_of_week:
                return None
            h, m = map(int, sched.time_of_day.split(":"))
            for delta in range(7):
                candidate = (now + timedelta(days=delta)).replace(hour=h, minute=m, second=0, microsecond=0)
                if candidate > now and candidate.weekday() in sched.days_of_week:
                    return candidate.isoformat(timespec="seconds")
            return None

        elif sched.schedule_type == ScheduleType.hourly:
            minute = sched.minute_of_hour if sched.minute_of_hour is not None else 0
            candidate = now.replace(minute=minute, second=0, microsecond=0)
            if candidate <= now:
                candidate += timedelta(hours=1)
            return candidate.isoformat(timespec="seconds")

        return None

    async def _check_and_trigger_schedules(self) -> None:
        now = datetime.now()
        for sched in self.list_schedules():
            if not sched.enabled:
                continue
            if not sched.next_run_at:
                sched.next_run_at = self._compute_next_run_at(sched)
                sched.save(self.schedules_dir)
                continue
            try:
                dt = datetime.fromisoformat(sched.next_run_at)
            except Exception:
                continue
            if now >= dt:
                await self._trigger_schedule(sched)

    async def _trigger_schedule(self, sched: Schedule) -> None:
        try:
            task = await self.submit(
                prompt=sched.prompt,
                project_name=sched.project_name,
                auto=sched.auto,
                account_name=sched.account,
            )
            sched.last_run_at = datetime.now().isoformat(timespec="seconds")
            sched.last_task_id = task.id
            sched.next_run_at = self._compute_next_run_at(sched)
            sched.updated = datetime.now().isoformat(timespec="seconds")
            sched.save(self.schedules_dir)
        except Exception as e:
            import traceback
            print(f"Error triggering schedule {sched.id}: {e}")
            traceback.print_exc()

    @staticmethod
    def new_conversation_id() -> str:
        ts   = datetime.now().strftime("%Y%m%d%H%M%S")
        rand = random.randint(0, 9999)
        return f"conv-{ts}-{rand:04d}"

    @staticmethod
    def new_board_id() -> str:
        ts   = datetime.now().strftime("%Y%m%d%H%M%S")
        rand = random.randint(0, 9999)
        return f"board-{ts}-{rand:04d}"

    @staticmethod
    def new_board_card_id() -> str:
        ts   = datetime.now().strftime("%Y%m%d%H%M%S")
        rand = random.randint(0, 9999)
        return f"card-{ts}-{rand:04d}"

    def list_boards(self) -> list[Board]:
        return Board.load_all(self.boards_dir)

    def get_board(self, board_id: str) -> Optional[Board]:
        path = self.boards_dir / f"{board_id}.json"
        if not path.exists():
            return None
        return Board.load(path)

    def create_board(self, name: str, project_name: str = "") -> Board:
        board = Board(
            id=self.new_board_id(),
            name=name.strip() or "开发看板",
            project_name=project_name.strip(),
        )
        board.save(self.boards_dir)
        return board

    def update_board(self, board_id: str, name: Optional[str] = None, project_name: Optional[str] = None) -> Board:
        board = self.get_board(board_id)
        if board is None:
            raise ValueError(f"看板 '{board_id}' 不存在")
        if name is not None:
            board.name = name.strip() or board.name
        if project_name is not None:
            board.project_name = project_name.strip()
        board.touch(self.boards_dir)
        return board

    def delete_board(self, board_id: str) -> None:
        board_path = self.boards_dir / f"{board_id}.json"
        if not board_path.exists():
            raise ValueError(f"看板 '{board_id}' 不存在")
        board_path.unlink()
        for card in self.list_board_cards(board_id=board_id, include_archived=True):
            self.delete_board_card(card.id)

    def list_board_cards(self, board_id: str = "", include_archived: bool = False) -> list[BoardCard]:
        cards = BoardCard.load_all(self.board_cards_dir)
        self._reconcile_board_cards_from_tasks(cards)
        if board_id:
            cards = [c for c in cards if c.board_id == board_id]
        if not include_archived:
            cards = [c for c in cards if not c.archived]
        return cards

    def _reconcile_board_cards_from_tasks(self, cards: list[BoardCard]) -> None:
        if not cards:
            return
        cards_by_id = {c.id: c for c in cards}
        for task in Task.load_all(self.tasks_dir):
            card_id = getattr(task, "board_card_id", "")
            if not card_id or card_id not in cards_by_id:
                continue
            self._sync_board_card_for_task(task, cards_by_id[card_id])

    def get_board_card(self, card_id: str) -> Optional[BoardCard]:
        path = self.board_cards_dir / f"{card_id}.json"
        if not path.exists():
            return None
        return BoardCard.load(path)

    def create_board_card(
        self,
        board_id: str,
        title: str,
        description: str = "",
        project_name: str = "",
        status: BoardCardStatus = BoardCardStatus.planned,
        priority: str = "normal",
    ) -> BoardCard:
        if self.get_board(board_id) is None:
            raise ValueError(f"看板 '{board_id}' 不存在")
        card = BoardCard(
            id=self.new_board_card_id(),
            board_id=board_id,
            title=title.strip() or "未命名专题",
            description=description.strip(),
            project_name=project_name.strip(),
            status=status,
            priority=priority.strip() or "normal",
        )
        card.save(self.board_cards_dir)
        return card

    def update_board_card(
        self,
        card_id: str,
        title: Optional[str] = None,
        description: Optional[str] = None,
        project_name: Optional[str] = None,
        status: Optional[BoardCardStatus] = None,
        priority: Optional[str] = None,
        conversation_id: Optional[str] = None,
        pipeline_id: Optional[str] = None,
        archived: Optional[bool] = None,
    ) -> BoardCard:
        card = self.get_board_card(card_id)
        if card is None:
            raise ValueError(f"看板卡片 '{card_id}' 不存在")
        if title is not None:
            card.title = title.strip() or card.title
        if description is not None:
            card.description = description.strip()
        if project_name is not None:
            card.project_name = project_name.strip()
        if status is not None:
            card.status = status
        if priority is not None:
            card.priority = priority.strip() or card.priority
        if conversation_id is not None:
            card.conversation_id = conversation_id.strip()
        if pipeline_id is not None:
            card.pipeline_id = pipeline_id.strip()
        if archived is not None:
            card.archived = archived
        card.touch(self.board_cards_dir)
        return card

    def delete_board_card(self, card_id: str) -> None:
        path = self.board_cards_dir / f"{card_id}.json"
        if not path.exists():
            raise ValueError(f"看板卡片 '{card_id}' 不存在")
        path.unlink()

    def add_task_to_board_card(self, card_id: str, task_id: str) -> BoardCard:
        card = self.get_board_card(card_id)
        if card is None:
            raise ValueError(f"看板卡片 '{card_id}' 不存在")
        if task_id not in card.task_ids:
            card.task_ids.append(task_id)
        task = self.get_task(task_id)
        if task is not None:
            if task.conversation_id and not card.conversation_id:
                card.conversation_id = task.conversation_id
            if task.pipeline_id and not card.pipeline_id:
                card.pipeline_id = task.pipeline_id
            if task.project_name and not card.project_name:
                card.project_name = task.project_name
            return self._sync_board_card_for_task(task, card)
        card.touch(self.board_cards_dir)
        return card

    def _sync_board_card_for_task(self, task: Task, card: Optional[BoardCard] = None) -> Optional[BoardCard]:
        card_id = getattr(task, "board_card_id", "")
        if not card_id and card is None:
            return None
        card = card or self.get_board_card(card_id)
        if card is None:
            return None

        changed = False
        if task.id not in card.task_ids:
            card.task_ids.append(task.id)
            changed = True
        if task.conversation_id and not card.conversation_id:
            card.conversation_id = task.conversation_id
            changed = True
        if task.pipeline_id and not card.pipeline_id:
            card.pipeline_id = task.pipeline_id
            changed = True
        if task.project_name and not card.project_name:
            card.project_name = task.project_name
            changed = True

        if task.status in (TaskStatus.pending, TaskStatus.scheduled) and card.status == BoardCardStatus.planned:
            card.status = BoardCardStatus.todo
            changed = True
        elif task.status == TaskStatus.running and card.status in (BoardCardStatus.planned, BoardCardStatus.todo):
            card.status = BoardCardStatus.running
            changed = True
        elif task.status == TaskStatus.done and card.status == BoardCardStatus.running:
            card.status = BoardCardStatus.review
            changed = True

        if changed:
            card.touch(self.board_cards_dir)
        return card

    def list_conversations(self, include_archived: bool = False) -> list[Conversation]:
        convs = Conversation.load_all(self.conversations_dir)
        if not include_archived:
            convs = [c for c in convs if c.status != ConversationStatus.archived]
        return convs

    def archive_conversation(self, conversation_id: str, status: ConversationStatus) -> Conversation:
        conv = self.get_conversation(conversation_id)
        if conv is None:
            raise ValueError(f"任务链 '{conversation_id}' 不存在")
        conv.status = status
        conv.save(self.conversations_dir)
        return conv

    def rename_conversation(self, conversation_id: str, name: str) -> Conversation:
        conv = self.get_conversation(conversation_id)
        if conv is None:
            raise ValueError(f"任务链 '{conversation_id}' 不存在")
        conv.name = name.strip() or conv.name
        conv.save(self.conversations_dir)
        return conv

    def delete_conversation(self, conversation_id: str) -> None:
        path = self.conversations_dir / f"{conversation_id}.json"
        if not path.exists():
            raise ValueError(f"任务链 '{conversation_id}' 不存在")
        path.unlink()

    def get_conversation(self, conversation_id: str) -> Optional[Conversation]:
        path = self.conversations_dir / f"{conversation_id}.json"
        if not path.exists():
            return None
        return Conversation.load(path)

    def ensure_conversation_available(self, conversation: Conversation) -> None:
        for task in Task.load_all(self.tasks_dir):
            if task.conversation_id == conversation.id and task.status == TaskStatus.running:
                raise RuntimeError(f"任务链 '{conversation.name}' 正在运行，请等待当前任务结束")

    def update_conversation_native_session(
        self,
        conversation_id: str,
        native_session_id: str,
        task_id: str,
    ) -> None:
        conversation = self.get_conversation(conversation_id)
        if conversation is None:
            return
        conversation.touch(
            self.conversations_dir,
            native_session_id=native_session_id,
            last_task_id=task_id,
        )

    def _create_conversation(self, name: str, acc: Account, project: Project, task_project: str) -> Conversation:
        conversation = Conversation(
            id      = self.new_conversation_id(),
            name    = name,
            account = acc.name,
            type    = acc.type,
            project = task_project,
            project_name = project.name,
        )
        conversation.save(self.conversations_dir)
        return conversation

    # ── 提交任务 ──────────────────────────────────────────

    # ── 定时与排队调度 ────────────────────────────────────────

    def start_scheduling_loop(self) -> None:
        if self._loop_task is None or self._loop_task.done():
            self._loop_task = asyncio.create_task(
                self._schedule_pending_tasks_loop(),
                name="scheduler-pending-loop",
            )

    async def _schedule_pending_tasks_loop(self) -> None:
        while True:
            try:
                await self._check_and_trigger_schedules()
                await self.schedule_next_tasks()
                await self._check_auto_digest()
            except Exception as e:
                import traceback
                print("Error in schedule_next_tasks:")
                traceback.print_exc()
            await asyncio.sleep(1.0)

    async def _check_auto_digest(self) -> None:
        """Auto-generate the previous day's digest at 23:30 each night."""
        now = datetime.now()
        if now.hour != 23 or now.minute != 30:
            return
        yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
        if self._last_auto_digest_date == yesterday:
            return
        self._last_auto_digest_date = yesterday

        digest_path = self.digests_dir / f"{yesterday}.json"
        if digest_path.exists():
            try:
                import json as _json
                data = _json.loads(digest_path.read_text(encoding="utf-8"))
                if data.get("ai_summary") or data.get("status") in ("generating", "ready"):
                    return
            except Exception:
                pass

        projects = [p for p in self.get_projects() if p.active]
        if not projects:
            return

        try:
            from coderfleet.server.digest import (
                build_generate_prompt,
                compute_daily_stats,
            )
            from coderfleet.server.models import DailyDigest, DigestStatus

            stats = compute_daily_stats(yesterday, self.tasks_dir)
            if stats.total_done + stats.total_failed + stats.total_killed == 0:
                return
            prompt = build_generate_prompt(stats)
            task = await self.submit(prompt=prompt, project_name=projects[0].name, auto=True)
            record = DailyDigest(date=yesterday, ai_task_id=task.id, status=DigestStatus.generating)
            record.save(self.digests_dir)
            print(f"[digest] Auto-generating digest for {yesterday} via task {task.id}")
        except Exception as e:
            import traceback
            print(f"[digest] Auto-generation failed for {yesterday}: {e}")
            traceback.print_exc()

    async def schedule_next_tasks(self) -> None:
        now = datetime.now()

        # 1. 扫描并触发已到时间的定时任务 (scheduled -> pending)
        all_tasks = Task.load_all(self.tasks_dir)
        for t in all_tasks:
            if t.status == TaskStatus.scheduled and t.execute_at:
                try:
                    dt = datetime.fromisoformat(t.execute_at)
                    if now >= dt:
                        t.update_status(TaskStatus.pending, self.tasks_dir)
                except Exception as e:
                    t.update_status(TaskStatus.failed, self.tasks_dir)
                    self._write_failed_log(t, f"定时时间解析失败：{e}")

        # 2. 扫描并运行 pending 任务
        all_pending = Task.load_all(self.tasks_dir)
        task_map = {t.id: t for t in all_pending}
        pending_tasks = [t for t in all_pending if t.status == TaskStatus.pending]
        if not pending_tasks:
            return

        # 按照创建时间升序排列，先进先出
        pending_tasks.sort(key=lambda t: t.created or "")

        # conversation 级串行约束：同一 conversation 同时只能有一个任务在跑
        busy_conv_ids = {
            t.conversation_id for t in all_pending
            if t.status == TaskStatus.running and t.conversation_id
        }
        # 本轮已派发的 conversation，防止同一轮调度把同一 conversation 的多个 pending 同时拉起
        in_flight_conv_ids: set[str] = set()

        for task in pending_tasks:
            # ── depends_on 检查 ──────────────────────────────
            if task.depends_on:
                deps = [task_map.get(dep_id) for dep_id in task.depends_on]
                # 任意前置任务失败/终止 → 级联失败
                if any(d is None or d.status in (TaskStatus.failed, TaskStatus.killed) for d in deps):
                    task.update_status(TaskStatus.failed, self.tasks_dir)
                    self._write_failed_log(task, "前置任务失败或不存在，级联失败")
                    continue
                # 前置任务尚未全部完成 → 继续等待
                if not all(d.status == TaskStatus.done for d in deps):
                    continue

            # 有 conversation 的任务：同一 conversation 内保持串行
            if task.conversation_id:
                if task.conversation_id in busy_conv_ids or task.conversation_id in in_flight_conv_ids:
                    continue
                in_flight_conv_ids.add(task.conversation_id)

            # 异步拉起执行该 pending 任务
            await self._start_pending_task(task)

    async def _start_pending_task(self, task: Task) -> None:
        try:
            acc = next((a for a in self.get_accounts() if a.name == task.account), None)
            if acc is None:
                task.update_status(TaskStatus.failed, self.tasks_dir)
                self._write_failed_log(task, f"账号 '{task.account}' 不存在")
                return

            project = self.find_project_for_path(task.project, task.account)
            if project is None:
                task.update_status(TaskStatus.failed, self.tasks_dir)
                self._write_failed_log(task, f"项目路径 '{task.project}' 未配置")
                return

            container_name = project.container_name(acc.type)
            container_workdir = self.container_workdir_for_project(project, task.project)

            if not docker_mgr.is_container_running(container_name):
                task.update_status(TaskStatus.failed, self.tasks_dir)
                self._write_failed_log(task, f"容器 {container_name} 未运行")
                return

            # 加载对应的 conversation（如果有）
            conversation = self.get_conversation(task.conversation_id) if task.conversation_id else None

            # 转换为运行状态
            task.update_status(TaskStatus.running, self.tasks_dir)
            self._sync_board_card_for_task(task)

            # 写日志头
            log_path = self.get_log_path(task.id)
            self._write_log_header(log_path, task, acc, container_workdir, container_name)

            # 异步后台执行
            bg = asyncio.create_task(
                self._run(
                    task,
                    acc,
                    log_path,
                    getattr(task, "auto", False),
                    conversation,
                    container_workdir,
                    container_name,
                    getattr(task, "images", []),
                ),
                name=f"task-{task.id}",
            )
            self._running[task.id] = bg

        except Exception as e:
            task.update_status(TaskStatus.failed, self.tasks_dir)
            self._write_failed_log(task, f"拉起任务失败：{e}")

    def _write_failed_log(self, task: Task, reason: str) -> None:
        log_path = self.get_log_path(task.id)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("w", encoding="utf-8") as f:
            f.write("=== CoderFleet Task Log ===\n")
            f.write(f"id:      {task.id}\n")
            f.write(f"status:  failed\n")
            f.write(f"prompt:  {task.prompt}\n")
            f.write(f"error:   {reason}\n")
            f.write("=" * 38 + "\n\n")
            f.write(f"任务启动失败：{reason}\n")

    async def submit(
        self,
        prompt:         str,
        account_name:   Optional[str]         = None,
        prefer_project: Optional[str]         = None,
        prefer_type:    Optional[AccountType] = None,
        auto:           bool                  = False,
        conversation_id: Optional[str]         = None,
        conversation_name: Optional[str]       = None,
        project_name:   Optional[str]         = None,
        images:         list[str]             = [],
        execute_at:     Optional[str]         = None,
        parent_task_id: Optional[str]         = None,
        depends_on:     list[str]             = [],
        pipeline_id:    Optional[str]         = None,
        board_card_id:  Optional[str]         = None,
    ) -> Task:
        """
        提交任务，异步在后台执行，立即返回 Task 对象。
        调用方可以通过 task.id 跟踪进度。
        """
        is_pending = False
        conversation: Optional[Conversation] = None
        card_id = board_card_id or ""
        if card_id and self.get_board_card(card_id) is None:
            raise ValueError(f"看板卡片 '{card_id}' 不存在")

        if conversation_id:
            conversation = self.get_conversation(conversation_id)
            if conversation is None:
                raise ValueError(f"任务链 '{conversation_id}' 不存在")

            # 判断任务链是否有正在运行的任务
            has_running_in_conv = any(
                t.conversation_id == conversation.id and t.status == TaskStatus.running
                for t in Task.load_all(self.tasks_dir)
            )
            if has_running_in_conv:
                is_pending = True

            account_name = conversation.account
            prefer_type = conversation.type
            prefer_project = conversation.project
            project_name = conversation.project_name or project_name

        selected_project: Optional[Project] = None
        if project_name:
            selected_project = self.find_project_by_name(project_name)
            if selected_project is None:
                raise ValueError(f"项目 '{project_name}' 不存在")
            if account_name and account_name != selected_project.account:
                raise ValueError(
                    f"项目 '{project_name}' 关联账号为 {selected_project.account}，与指定账号 {account_name} 不一致"
                )
            account_name = selected_project.account
            prefer_project = selected_project.path

        # 确定账号
        acc: Optional[Account] = None
        if account_name:
            acc = next((a for a in self.get_accounts() if a.name == account_name), None)
            if acc is None:
                raise ValueError(f"账号 '{account_name}' 不存在")
            if prefer_type and acc.type != prefer_type:
                raise ValueError(
                    f"账号 '{account_name}' 类型为 {acc.type.value}，与筛选类型 {prefer_type.value} 不一致"
                )
            # 账号级不再限制并发，同一账号的不同 conversation 可以同时跑
        else:
            acc = self.find_idle_account(
                prefer_type    = prefer_type,
                prefer_project = prefer_project,
            )
            if acc is None:
                hints = []
                if prefer_project:
                    hints.append(f"项目：{prefer_project}")
                if prefer_type:
                    hints.append(f"类型：{prefer_type.value}")
                hint_str = "，".join(hints)
                raise RuntimeError(
                    f"没有匹配的可用账号{('（' + hint_str + '）') if hint_str else ''}"
                )

        task_project = self.resolve_task_project(acc, prefer_project)
        selected_project = selected_project or self.find_project_for_path(task_project, acc.name)
        if selected_project is None:
            raise ValueError(f"项目 '{task_project}' 未配置")
        container_name = selected_project.container_name(acc.type)
        container_workdir = self.container_workdir_for_project(selected_project, task_project)

        if not docker_mgr.is_container_running(container_name):
            raise RuntimeError(f"容器 {container_name} 未运行")

        if conversation is None and conversation_name:
            conversation = self._create_conversation(conversation_name, acc, selected_project, task_project)

        # 创建任务记录
        task_id  = self.new_task_id()

        # ── DAG 字段 ──
        dag_parent   = parent_task_id or ""
        dag_depends  = list(depends_on)
        dag_pipeline = pipeline_id or ""

        # ── 处理定时任务 ──
        if execute_at:
            try:
                dt = datetime.fromisoformat(execute_at)
                if dt > datetime.now():
                    task = Task(
                        id           = task_id,
                        status       = TaskStatus.scheduled,
                        account      = acc.name,
                        type         = acc.type,
                        prompt       = prompt,
                        project      = task_project,
                        project_name = selected_project.name,
                        conversation_id = conversation.id if conversation else "",
                        native_session_id = conversation.native_session_id if conversation else "",
                        auto         = auto,
                        images       = images,
                        execute_at   = execute_at,
                        parent_task_id = dag_parent,
                        depends_on     = dag_depends,
                        pipeline_id    = dag_pipeline,
                        board_card_id  = card_id,
                    )
                    task.save(self.tasks_dir)
                    if dag_pipeline:
                        self._register_task_in_pipeline(task_id, dag_pipeline)
                    if card_id:
                        self.add_task_to_board_card(card_id, task_id)

                    # 写入空日志文件防 SSE 404
                    log_path = self.get_log_path(task_id)
                    log_path.parent.mkdir(parents=True, exist_ok=True)
                    log_path.write_text("", encoding="utf-8")

                    return task
            except ValueError:
                pass

        # ── 处理排队任务 ──
        if is_pending:
            task = Task(
                id           = task_id,
                status       = TaskStatus.pending,
                account      = acc.name,
                type         = acc.type,
                prompt       = prompt,
                project      = task_project,
                project_name = selected_project.name,
                conversation_id = conversation.id if conversation else "",
                native_session_id = conversation.native_session_id if conversation else "",
                auto         = auto,
                images       = images,
                parent_task_id = dag_parent,
                depends_on     = dag_depends,
                pipeline_id    = dag_pipeline,
                board_card_id  = card_id,
            )
            task.save(self.tasks_dir)
            if dag_pipeline:
                self._register_task_in_pipeline(task_id, dag_pipeline)
            if card_id:
                self.add_task_to_board_card(card_id, task_id)

            # 写入空日志文件防 SSE 404
            log_path = self.get_log_path(task_id)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text("", encoding="utf-8")

            # 立即触发一次调度检查
            asyncio.create_task(self.schedule_next_tasks())
            return task

        # ── 立即执行任务 ──
        log_path = self.get_log_path(task_id)
        task = Task(
            id           = task_id,
            status       = TaskStatus.running,
            account      = acc.name,
            type         = acc.type,
            prompt       = prompt,
            project      = task_project,
            project_name = selected_project.name,
            conversation_id = conversation.id if conversation else "",
            native_session_id = conversation.native_session_id if conversation else "",
            auto         = auto,
            images       = images,
            parent_task_id = dag_parent,
            depends_on     = dag_depends,
            pipeline_id    = dag_pipeline,
            board_card_id  = card_id,
        )
        task.save(self.tasks_dir)
        if dag_pipeline:
            self._register_task_in_pipeline(task_id, dag_pipeline)
        if card_id:
            self.add_task_to_board_card(card_id, task_id)

        # 写日志头
        self._write_log_header(log_path, task, acc, container_workdir, container_name)

        # 异步后台执行
        bg = asyncio.create_task(
            self._run(task, acc, log_path, auto, conversation, container_workdir, container_name, images),
            name=f"task-{task_id}",
        )
        self._running[task_id] = bg

        return task

    # ── 从已有任务创建任务链 ──────────────────────────────

    def create_conversation_from_task(
        self,
        name: str,
        task_id: str,
    ) -> Conversation:
        """
        从已有任务（需有 native_session_id）创建任务链。
        之后可通过 conversation_id 续接该会话上下文。
        """
        task = self.get_task(task_id)
        if task is None:
            raise ValueError(f"任务 '{task_id}' 不存在")
        if not task.native_session_id:
            raise ValueError(
                f"任务 '{task_id}' 没有 native_session_id，"
                "可能是较早的任务（未开启流式 JSON 输出模式）"
            )
        acc = next((a for a in self.get_accounts() if a.name == task.account), None)
        if acc is None:
            raise ValueError(f"账号 '{task.account}' 不存在")
        project = self.find_project_for_path(task.project, task.account)
        if project is None:
            raise ValueError(f"项目路径 '{task.project}' 未配置")

        conversation = Conversation(
            id               = self.new_conversation_id(),
            name             = name,
            account          = acc.name,
            type             = acc.type,
            project          = task.project,
            project_name     = project.name,
            native_session_id = task.native_session_id,
            last_task_id     = task_id,
        )
        conversation.save(self.conversations_dir)
        return conversation

    def _write_log_header(self, log_path: Path, task: Task, acc: Account, container_workdir: str = "", container_name: str = "") -> None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as f:
            f.write("=== CoderFleet Task Log ===\n")
            f.write(f"id:      {task.id}\n")
            f.write(f"account: {acc.name} ({acc.type.value})\n")
            f.write(f"project: {task.project}\n")
            if container_name:
                f.write(f"container: {container_name}\n")
            if container_workdir:
                f.write(f"container cwd: {container_workdir}\n")
            if task.conversation_id:
                f.write(f"conversation: {task.conversation_id}\n")
                if task.native_session_id:
                    f.write(f"native session: {task.native_session_id}\n")
            escaped_prompt = task.prompt.replace('\r\n', '\\n').replace('\n', '\\n') if task.prompt else ""
            f.write(f"prompt:  {escaped_prompt}\n")
            f.write(f"started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write("=" * 38 + "\n\n")

    async def _stream_container_log(
        self,
        task: Task,
        acc: Account,
        log_path: Path,
        host_log: Path,
        host_exit: Path,
        conversation: Optional[Conversation] = None,
        start_offset: int = 0,
    ) -> int:
        """
        宿主机侧日志轮询：将容器写入 host_log 的新内容同步到 log_path，
        检测到 host_exit 文件出现后退出，返回 exit code。
        CancelledError 不在此处捕获，由调用方处理 kill 逻辑。
        """
        import aiofiles
        last_size = start_offset
        captured_session_id = ""
        container_name = self._get_task_container(task) or ""

        async with aiofiles.open(log_path, mode="a", encoding="utf-8") as f:
            while True:
                await asyncio.sleep(0.3)
                is_done = host_exit.exists()

                if host_log.exists():
                    cur_size = host_log.stat().st_size
                    if cur_size > last_size:
                        async with aiofiles.open(host_log, mode="rb") as hf:
                            await hf.seek(last_size)
                            new_bytes = await hf.read()
                        text = new_bytes.decode("utf-8", errors="replace")
                        await f.write(text)
                        await f.flush()
                        last_size = cur_size

                        if not captured_session_id:
                            captured_session_id = self.extract_native_session_id(acc.type, text)
                            if captured_session_id:
                                task.native_session_id = captured_session_id
                                task.save(self.tasks_dir)
                                if conversation:
                                    self.update_conversation_native_session(
                                        conversation.id,
                                        captured_session_id,
                                        task.id,
                                    )

                if is_done:
                    break

        rc = -1
        if host_exit.exists():
            try:
                rc = int(host_exit.read_text().strip())
            except (ValueError, OSError):
                rc = -1

        if rc == 0:
            if conversation:
                if not conversation.native_session_id and captured_session_id:
                    self.update_conversation_native_session(conversation.id, captured_session_id, task.id)
                else:
                    conversation.touch(self.conversations_dir, last_task_id=task.id)
            task.update_status(TaskStatus.done, self.tasks_dir)
            self._sync_board_card_for_task(task)
            await self._append_usage_status(log_path, acc, container_name)
            self._append_log_footer(log_path, "done")
        else:
            task.update_status(TaskStatus.failed, self.tasks_dir)
            self._sync_board_card_for_task(task)
            await self._append_usage_status(log_path, acc, container_name)
            self._append_log_footer(log_path, f"failed (exit={rc})")

        self._extract_and_save_token_usage(task, log_path)
        asyncio.create_task(self._notify(task))
        return rc

    async def _run(
        self,
        task: Task,
        acc: Account,
        log_path: Path,
        auto: bool,
        conversation: Optional[Conversation] = None,
        container_workdir: str = "",
        container_name: str = "",
        images: list[str] = [],
    ) -> None:
        """后台协程：以 detached 方式启动容器任务，再轮询宿主机日志文件跟踪进度。"""
        try:
            cmd = self.build_cli_command(
                acc.type,
                task.prompt,
                auto,
                task.id,
                native_session_id=conversation.native_session_id if conversation else "",
                container_workdir=container_workdir,
                images=images,
            )

            project_env = {}
            if task.project_name:
                try:
                    project_env = self.get_project_env(task.project_name)
                except Exception:
                    pass

            docker_exec_args = ["docker", "exec"]
            for k, v in project_env.items():
                docker_exec_args += ["-e", f"{k}={v}"]
            docker_exec_args += [container_name, "bash", "-c", cmd]

            proc = await asyncio.create_subprocess_exec(
                *docker_exec_args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            await proc.wait()

            if proc.returncode != 0:
                err = (await proc.stdout.read()).decode("utf-8", errors="replace") if proc.stdout else ""
                raise RuntimeError(f"docker exec failed (exit={proc.returncode}): {err.strip()}")

            project_root = self._get_project_root(task)
            if project_root is None:
                raise RuntimeError(f"找不到任务 {task.id} 对应的项目根目录")

            host_log  = self._host_task_log(project_root, task.id)
            host_exit = self._host_task_exit(project_root, task.id)

            await self._stream_container_log(
                task, acc, log_path, host_log, host_exit, conversation
            )

        except asyncio.CancelledError:
            self.kill_task_process(task)
            task.update_status(TaskStatus.killed, self.tasks_dir)
            self._sync_board_card_for_task(task)
            self._append_log_footer(log_path, "killed")
            asyncio.create_task(self._notify(task))
            return
        except Exception as e:
            task.update_status(TaskStatus.failed, self.tasks_dir)
            self._sync_board_card_for_task(task)
            await self._append_usage_status(log_path, acc, container_name)
            self._append_log_footer(log_path, f"failed: {e}")
            asyncio.create_task(self._notify(task))
            return
        finally:
            self._running.pop(task.id, None)
            self._cleanup_container_task_files(task)
            asyncio.create_task(self.schedule_next_tasks())

    async def _reattach(self, task: Task) -> None:
        """重新 attach 到一个在 Python 重启后仍存活的容器进程，继续跟踪其日志。"""
        log_path = self.get_log_path(task.id)

        acc = next((a for a in self.get_accounts() if a.name == task.account), None)
        if acc is None:
            task.update_status(TaskStatus.failed, self.tasks_dir)
            self._sync_board_card_for_task(task)
            self._append_log_footer(log_path, "failed: account not found on reattach")
            return

        project_root = self._get_project_root(task)
        if project_root is None:
            task.update_status(TaskStatus.failed, self.tasks_dir)
            self._sync_board_card_for_task(task)
            self._append_log_footer(log_path, "failed: project root not found on reattach")
            return

        host_log  = self._host_task_log(project_root, task.id)
        host_exit = self._host_task_exit(project_root, task.id)
        conversation = self.get_conversation(task.conversation_id) if task.conversation_id else None
        start_offset = host_log.stat().st_size if host_log.exists() else 0

        try:
            self._append_log_footer(log_path, "reattached after server restart")
            await self._stream_container_log(
                task, acc, log_path, host_log, host_exit, conversation,
                start_offset=start_offset,
            )
        except asyncio.CancelledError:
            self.kill_task_process(task)
            task.update_status(TaskStatus.killed, self.tasks_dir)
            self._sync_board_card_for_task(task)
            self._append_log_footer(log_path, "killed")
            asyncio.create_task(self._notify(task))
            return
        except Exception as e:
            task.update_status(TaskStatus.failed, self.tasks_dir)
            self._sync_board_card_for_task(task)
            self._append_log_footer(log_path, f"failed during reattach: {e}")
            asyncio.create_task(self._notify(task))
            return
        finally:
            self._running.pop(task.id, None)
            self._cleanup_container_task_files(task)
            asyncio.create_task(self.schedule_next_tasks())

    async def _append_usage_status(self, log_path: Path, acc: Account, container_name: str) -> None:
        cmd = self.build_usage_status_command(acc.type)
        if not cmd or not container_name:
            return

        try:
            with log_path.open("a", encoding="utf-8") as f:
                f.write("\n" + "=" * 38 + "\n")
                f.write("usage status:\n")

            proc = await asyncio.create_subprocess_exec(
                "docker",
                "exec",
                container_name,
                "bash",
                "-lc",
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=45)
            text = stdout.decode("utf-8", errors="replace").strip()
            if not text:
                text = "未获取到用量信息"
        except Exception as e:
            text = f"用量检查失败：{e}"

        try:
            with log_path.open("a", encoding="utf-8") as f:
                f.write(text + "\n")
        except Exception:
            pass

    def _append_log_footer(self, log_path: Path, result: str) -> None:
        try:
            with log_path.open("a", encoding="utf-8") as f:
                f.write("\n" + "=" * 38 + "\n")
                f.write(f"finished: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} [{result}]\n")
        except Exception:
            pass

    def _extract_and_save_token_usage(self, task: Task, log_path: Path) -> None:
        """Parse completed task log to extract token usage and persist it in the Task JSON."""
        try:
            from coderfleet.server.log_parser import parse_log
            log_text = log_path.read_text(encoding="utf-8", errors="ignore")
            data = parse_log(log_text, task.type.value)
            if data.tokens_input or data.tokens_output or data.cost_usd:
                task.tokens_input  = data.tokens_input
                task.tokens_output = data.tokens_output
                task.cost_usd      = data.cost_usd
                task.save(self.tasks_dir)
        except Exception:
            pass

    async def _get_hermes_session_id(self, container_name: str) -> str:
        """Query the most recently updated hermes session ID from inside the container."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "exec", container_name, "bash", "-lc",
                "/opt/hermes-venv/bin/hermes sessions export 2>/dev/null | tail -1",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            text = stdout.decode("utf-8", errors="replace").strip()
            if text:
                data = json.loads(text)
                return str(data.get("id", ""))
        except Exception:
            pass
        return ""

    def _get_task_container(self, task: Task) -> Optional[str]:
        """通过任务记录的 project 路径找到对应项目，推导容器名。"""
        project = self.find_project_for_path(task.project, task.account)
        if project is None:
            return None
        return project.container_name(task.type)

    def _get_project_root(self, task: Task) -> Optional[Path]:
        """返回任务所属项目在宿主机的根目录（/workspace 在容器内挂载的目标）。"""
        project = self.find_project_for_path(task.project, task.account)
        if project is None:
            return None
        return Path(project.path)

    @staticmethod
    def _host_task_log(project_root: Path, task_id: str) -> Path:
        return project_root / ".coderfleet-tasks" / f"{task_id}.log"

    @staticmethod
    def _host_task_exit(project_root: Path, task_id: str) -> Path:
        return project_root / ".coderfleet-tasks" / f"{task_id}.exit"

    def _cleanup_container_task_files(self, task: Task) -> None:
        project_root = self._get_project_root(task)
        if project_root is None:
            return
        self._host_task_log(project_root, task.id).unlink(missing_ok=True)
        self._host_task_exit(project_root, task.id).unlink(missing_ok=True)

    def is_task_process_alive(self, task: Task) -> bool:
        container = self._get_task_container(task)
        if container is None:
            return False
        marker = shlex.quote(self.task_process_marker(task.id))
        result = subprocess.run(
            [
                "docker",
                "exec",
                container,
                "bash",
                "-lc",
                f"pgrep -af {marker} >/dev/null",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return result.returncode == 0

    def kill_task_process(self, task: Task) -> None:
        container = self._get_task_container(task)
        if container is None:
            return
        marker = shlex.quote(self.task_process_marker(task.id))
        subprocess.run(
            [
                "docker",
                "exec",
                container,
                "bash",
                "-lc",
                f"pkill -TERM -f {marker} || true",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )

    async def reconcile_running_tasks(self) -> int:
        """
        服务启动时调用：对状态仍为 running 的任务做恢复处理。
        - 容器进程仍存活 → 重新 attach，任务继续执行
        - 容器进程已消亡 → 读 exit 文件恢复最终状态，或标记 failed
        """
        reconciled = 0
        for task in Task.load_all(self.tasks_dir):
            if task.status != TaskStatus.running:
                continue

            if self.is_task_process_alive(task):
                bg = asyncio.create_task(
                    self._reattach(task),
                    name=f"task-{task.id}",
                )
                self._running[task.id] = bg
                reconciled += 1
                continue

            # 进程已消亡：尝试从 exit 文件恢复状态
            project_root = self._get_project_root(task)
            if project_root is not None:
                exit_file = self._host_task_exit(project_root, task.id)
                if exit_file.exists():
                    try:
                        rc = int(exit_file.read_text().strip())
                        status = TaskStatus.done if rc == 0 else TaskStatus.failed
                        result = "done" if rc == 0 else f"failed (exit={rc})"
                    except (ValueError, OSError):
                        status = TaskStatus.failed
                        result = "failed: server restarted; could not read exit code"
                else:
                    status = TaskStatus.failed
                    result = "failed: server restarted; no container process or exit file found"
            else:
                status = TaskStatus.failed
                result = "failed: server restarted; project root not found"

            task.update_status(status, self.tasks_dir)
            self._sync_board_card_for_task(task)
            self._append_log_footer(self.get_log_path(task.id), result)
            self._cleanup_container_task_files(task)
            reconciled += 1

        return reconciled

    # ── 终止任务 ──────────────────────────────────────────

    async def kill_task(self, task_id: str) -> Task:
        task = self.get_task(task_id)
        if task is None:
            raise ValueError(f"任务 '{task_id}' 不存在")
        if task.status not in (TaskStatus.running, TaskStatus.pending, TaskStatus.scheduled):
            raise RuntimeError(f"任务状态为 '{task.status.value}'，只能终止 running、pending 或 scheduled 状态的任务")

        if task.status in (TaskStatus.pending, TaskStatus.scheduled):
            old_status = task.status
            task.update_status(TaskStatus.killed, self.tasks_dir)
            self._sync_board_card_for_task(task)
            reason = "killed by user (cancelled schedule)" if old_status == TaskStatus.scheduled else "killed by user (while pending)"
            self._append_log_footer(self.get_log_path(task_id), reason)
            # 异步触发一次调度，确保释放该队列的后续处理（以防万一）
            asyncio.create_task(self.schedule_next_tasks())
            return task

        # 先更新状态防止并发写入
        task.update_status(TaskStatus.killed, self.tasks_dir)
        self._sync_board_card_for_task(task)

        # 取消后台协程
        self.kill_task_process(task)
        bg = self._running.pop(task_id, None)
        if bg and not bg.done():
            bg.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(bg), timeout=3)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass

        self._append_log_footer(self.get_log_path(task_id), "killed by user")
        # 触发下一次调度
        asyncio.create_task(self.schedule_next_tasks())
        return task

    def delete_task(self, task_id: str) -> None:
        task = self.get_task(task_id)
        if task and task.status == TaskStatus.running:
            raise RuntimeError(f"任务 '{task_id}' 正在运行，无法删除")
        json_path = self.tasks_dir / f"{task_id}.json"
        log_path  = self.tasks_dir / f"{task_id}.log"
        if not json_path.exists():
            raise ValueError(f"任务 '{task_id}' 不存在")
        json_path.unlink(missing_ok=True)
        log_path.unlink(missing_ok=True)

    def archive_task(self, task_id: str, archived: bool) -> Task:
        task = self.get_task(task_id)
        if task is None:
            raise ValueError(f"任务 '{task_id}' 不存在")
        task.archived = archived
        task.save(self.tasks_dir)
        return task

    def _register_task_in_pipeline(self, task_id: str, pipeline_id: str) -> None:
        """将任务 ID 注册到 pipeline 的 task_ids 列表（幂等）。"""
        try:
            pipeline = self.get_pipeline(pipeline_id)
            if pipeline and task_id not in pipeline.task_ids:
                pipeline.task_ids.append(task_id)
                pipeline.touch(self.pipelines_dir)
        except Exception:
            pass

    # ── 清理旧记录 ────────────────────────────────────────

    def clean_tasks(self, keep: int = 30) -> int:
        all_tasks = Task.load_all(self.tasks_dir)
        if len(all_tasks) <= keep:
            return 0
        cleaned = 0
        for task in all_tasks[keep:]:
            if task.status == TaskStatus.running:
                continue
            json_path = self.tasks_dir / f"{task.id}.json"
            log_path  = self.tasks_dir / f"{task.id}.log"
            json_path.unlink(missing_ok=True)
            log_path.unlink(missing_ok=True)
            cleaned += 1
        return cleaned

    # ── Pipeline CRUD ─────────────────────────────────────

    @staticmethod
    def new_pipeline_id() -> str:
        ts   = datetime.now().strftime("%Y%m%d%H%M%S")
        rand = random.randint(0, 9999)
        return f"pipe-{ts}-{rand:04d}"

    def list_pipelines(self) -> list[Pipeline]:
        return Pipeline.load_all(self.pipelines_dir)

    def get_pipeline(self, pipeline_id: str) -> Optional[Pipeline]:
        path = self.pipelines_dir / f"{pipeline_id}.json"
        if not path.exists():
            return None
        return Pipeline.load(path)

    def create_pipeline(self, name: str, task_ids: list[str] = [], project_name: str = "") -> Pipeline:
        pipeline = Pipeline(
            id           = self.new_pipeline_id(),
            name         = name,
            project_name = project_name,
            task_ids     = list(task_ids),
        )
        pipeline.save(self.pipelines_dir)
        # 反向更新已有任务的 pipeline_id
        for tid in task_ids:
            task = self.get_task(tid)
            if task and not task.pipeline_id:
                task.pipeline_id = pipeline.id
                task.save(self.tasks_dir)
        return pipeline

    def add_task_to_pipeline(self, pipeline_id: str, task_id: str) -> Pipeline:
        pipeline = self.get_pipeline(pipeline_id)
        if pipeline is None:
            raise ValueError(f"工作流 '{pipeline_id}' 不存在")
        if task_id not in pipeline.task_ids:
            pipeline.task_ids.append(task_id)
        pipeline.touch(self.pipelines_dir)
        # 更新任务记录
        task = self.get_task(task_id)
        if task and not task.pipeline_id:
            task.pipeline_id = pipeline_id
            task.save(self.tasks_dir)
        return pipeline

    def delete_pipeline(self, pipeline_id: str) -> None:
        path = self.pipelines_dir / f"{pipeline_id}.json"
        if not path.exists():
            raise ValueError(f"工作流 '{pipeline_id}' 不存在")
        path.unlink()

    # ── 工作流模板 CRUD ───────────────────────────────────

    @staticmethod
    def new_template_id() -> str:
        import random
        return "tpl-" + datetime.now().strftime("%Y%m%d%H%M%S") + f"-{random.randint(1000,9999)}"

    def list_templates(self) -> list[WorkflowTemplate]:
        return WorkflowTemplate.load_all(self.templates_dir)

    def get_template(self, template_id: str) -> Optional[WorkflowTemplate]:
        path = self.templates_dir / f"{template_id}.json"
        return WorkflowTemplate.load(path) if path.exists() else None

    def create_template(self, name: str, description: str, nodes: list[TemplateNode]) -> WorkflowTemplate:
        tpl = WorkflowTemplate(id=self.new_template_id(), name=name, description=description, nodes=nodes)
        tpl.save(self.templates_dir)
        return tpl

    def update_template(self, template_id: str, name: str, description: str, nodes: list[TemplateNode]) -> WorkflowTemplate:
        tpl = self.get_template(template_id)
        if tpl is None:
            raise ValueError(f"模板 '{template_id}' 不存在")
        tpl.name = name
        tpl.description = description
        tpl.nodes = nodes
        tpl.touch(self.templates_dir)
        return tpl

    def delete_template(self, template_id: str) -> None:
        path = self.templates_dir / f"{template_id}.json"
        if not path.exists():
            raise ValueError(f"模板 '{template_id}' 不存在")
        path.unlink()

    # ── 模板执行（触发一次运行） ──────────────────────────

    @staticmethod
    def _topo_sort_nodes(nodes: list[TemplateNode]) -> list[TemplateNode]:
        node_map  = {n.node_id: n for n in nodes}
        visited:  set[str] = set()
        result:   list[TemplateNode] = []

        def visit(nid: str) -> None:
            if nid in visited:
                return
            visited.add(nid)
            node = node_map.get(nid)
            if node:
                for dep in node.depends_on:
                    visit(dep)
                result.append(node)

        for node in nodes:
            visit(node.node_id)
        return result

    async def run_template(
        self,
        template_id:     str,
        input_str:       str,
        project_map:     dict[str, str],  # role/node_id → project_name
        default_project: str = "",
    ) -> Pipeline:
        tpl = self.get_template(template_id)
        if tpl is None:
            raise ValueError(f"模板 '{template_id}' 不存在")
        if not tpl.nodes:
            raise ValueError("模板没有任何节点，无法执行")

        sorted_nodes = self._topo_sort_nodes(tpl.nodes)
        resolved_projects: dict[str, str] = {}
        for node in sorted_nodes:
            target_mode = getattr(node, "target_mode", "default") or "default"
            if target_mode == "fixed_project":
                project_name = node.project_name
            elif target_mode == "runtime_role":
                project_name = project_map.get(node.project_role) or project_map.get(node.node_id) or ""
            else:
                project_name = project_map.get(node.node_id) or default_project

            if not project_name:
                label = node.name or node.node_id
                raise ValueError(f"节点「{label}」未指定执行项目")
            if self.find_project_by_name(project_name) is None:
                label = node.name or node.node_id
                raise ValueError(f"节点「{label}」配置的项目 '{project_name}' 不存在")
            resolved_projects[node.node_id] = project_name

        # 按拓扑层级分组（同层节点并发，跨层串行并传递输出）
        from collections import defaultdict
        dep_levels: dict[str, int] = {}
        for node in sorted_nodes:
            level = max((dep_levels.get(d, -1) for d in (node.depends_on or [])), default=-1) + 1
            dep_levels[node.node_id] = level

        level_groups: dict[int, list[TemplateNode]] = defaultdict(list)
        for node in sorted_nodes:
            level_groups[dep_levels[node.node_id]].append(node)

        # 创建此次运行的 Pipeline（只含元数据，task_ids 由后台协程逐步追加）
        run_name = f"{tpl.name} · {input_str[:30]}" if input_str else tpl.name
        pipeline = Pipeline(
            id            = self.new_pipeline_id(),
            name          = run_name,
            template_id   = tpl.id,
            trigger_input = input_str,
        )
        pipeline.save(self.pipelines_dir)

        # 后台协程按层推进：等待上层完成 → 提取输出 → 插值提交下层
        asyncio.create_task(
            self._execute_pipeline_levels(
                pipeline.id, tpl, level_groups, resolved_projects, input_str
            ),
            name=f"pipeline-{pipeline.id}",
        )

        return pipeline

    # ── 模板执行辅助 ──────────────────────────────────────────────

    @staticmethod
    def _render_prompt(prompt_tpl: str, input_str: str, node_outputs: dict[str, str]) -> str:
        """
        Replace {{input}} and {{steps.<node_id>.outputs.text}} placeholders.
        Unknown step references are replaced with a readable placeholder.
        """
        result = prompt_tpl.replace("{{input}}", input_str)
        result = re.sub(
            r"\{\{steps\.([^}]+)\.outputs\.text\}\}",
            lambda m: node_outputs.get(m.group(1), f"[output of {m.group(1)} not available]"),
            result,
        )
        return result

    async def _wait_for_task_done(
        self,
        task_id: str,
        poll_interval: float = 2.0,
        timeout: float = 7200.0,
    ) -> "TaskStatus":
        """Poll until the task reaches a terminal state; return that status."""
        elapsed = 0.0
        while elapsed < timeout:
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval
            task = self.get_task(task_id)
            if task is None:
                return TaskStatus.failed
            if task.status in (TaskStatus.done, TaskStatus.failed, TaskStatus.killed):
                return task.status
        return TaskStatus.failed

    async def _submit_pipeline_node(
        self,
        node: "TemplateNode",
        pipeline_id: str,
        resolved_projects: dict[str, str],
        input_str: str,
        node_outputs: dict[str, str],
    ) -> "Task":
        """Submit a single node's task and register it in the pipeline."""
        actual_prompt = self._render_prompt(node.prompt_tpl, input_str, node_outputs)
        pname = resolved_projects[node.node_id]

        task = await self.submit(
            prompt       = actual_prompt,
            project_name = pname,
            auto         = True,
            pipeline_id  = pipeline_id,
        )

        # Update pipeline: add task_id + node_run record
        pipeline = self.get_pipeline(pipeline_id)
        if pipeline:
            if task.id not in pipeline.task_ids:
                pipeline.task_ids.append(task.id)
            # Replace any previous node_run for this node (retry case)
            pipeline.node_runs = [r for r in pipeline.node_runs if r.node_id != node.node_id]
            pipeline.node_runs.append(PipelineNodeRun(
                node_id          = node.node_id,
                node_name        = node.name,
                task_id          = task.id,
                target_mode      = getattr(node, "target_mode", "default") or "default",
                project_name     = getattr(node, "project_name", ""),
                project_role     = getattr(node, "project_role", ""),
                resolved_project = pname,
                actual_prompt    = actual_prompt,
            ))
            pipeline.touch(self.pipelines_dir)

        return task

    async def _execute_pipeline_levels(
        self,
        pipeline_id:       str,
        tpl:               "WorkflowTemplate",
        level_groups:      dict,
        resolved_projects: dict[str, str],
        input_str:         str,
    ) -> None:
        """
        Background coroutine: execute workflow levels sequentially.

        For each level:
          1. Submit all nodes in the level concurrently.
          2. Wait for every node to reach a terminal state (with retry support).
          3. Extract each node's text output.
          4. Proceed to the next level, injecting outputs into prompts via
             {{steps.<node_id>.outputs.text}}.
        """
        from coderfleet.server.log_parser import parse_log

        node_outputs: dict[str, str] = {}
        pipeline_failed = False

        for level in sorted(level_groups.keys()):
            if pipeline_failed:
                break

            nodes: list[TemplateNode] = level_groups[level]

            # --- Submit all nodes in this level concurrently ---
            submit_results = await asyncio.gather(
                *[
                    self._submit_pipeline_node(
                        node, pipeline_id, resolved_projects, input_str, node_outputs
                    )
                    for node in nodes
                ],
                return_exceptions=True,
            )

            # --- Wait for each node to finish (with per-node retry) ---
            for node, submit_result in zip(nodes, submit_results):
                if isinstance(submit_result, Exception):
                    print(f"[pipeline {pipeline_id}] node {node.node_id} submit error: {submit_result}")
                    pipeline_failed = True
                    continue

                task: Task = submit_result
                max_retries      = getattr(node, "max_retries", 0) or 0
                retry_delay      = getattr(node, "retry_delay_seconds", 30) or 30
                attempt          = 0

                while True:
                    final_status = await self._wait_for_task_done(task.id)

                    if final_status == TaskStatus.done:
                        # Extract output text for downstream nodes
                        log_path = self.get_log_path(task.id)
                        if log_path.exists():
                            log_text = log_path.read_text(encoding="utf-8", errors="ignore")
                            data = parse_log(log_text, task.type.value)
                            node_outputs[node.node_id] = data.text
                        break

                    # Task failed / killed
                    if attempt < max_retries:
                        attempt += 1
                        print(
                            f"[pipeline {pipeline_id}] node {node.node_id} "
                            f"failed (attempt {attempt}/{max_retries + 1}), "
                            f"retrying in {retry_delay}s"
                        )
                        await asyncio.sleep(retry_delay)
                        try:
                            task = await self._submit_pipeline_node(
                                node, pipeline_id, resolved_projects, input_str, node_outputs
                            )
                        except Exception as e:
                            print(
                                f"[pipeline {pipeline_id}] node {node.node_id} "
                                f"retry submit failed: {e}"
                            )
                            pipeline_failed = True
                            break
                    else:
                        print(
                            f"[pipeline {pipeline_id}] node {node.node_id} "
                            f"permanently failed after {attempt + 1} attempt(s)"
                        )
                        pipeline_failed = True
                        break

    # ── 逻辑项目（按 path 分组） ──────────────────────────

    def get_logical_projects(self) -> list[LogicalProject]:
        projects = self.get_projects()
        accounts = self.get_accounts()
        acc_map  = {a.name: a for a in accounts}

        groups: dict[str, list[Project]] = {}
        for p in projects:
            canonical = str(Path(p.path).expanduser().resolve())
            groups.setdefault(canonical, []).append(p)

        result = []
        for canonical_path, projs in groups.items():
            entries = [
                LogicalProjectEntry(
                    name    = p.name,
                    account = p.account,
                    type    = acc_map[p.account].type.value if p.account in acc_map else "unknown",
                )
                for p in projs
            ]
            result.append(LogicalProject(
                path         = canonical_path,
                display_name = projs[0].name,
                projects     = entries,
            ))
        return result

    # ── 派生子任务 ────────────────────────────────────────

    async def spawn_subtask(
        self,
        parent_task_id: str,
        prompt:         str,
        project_name:   str,
        auto:           bool         = True,
        wait_for_parent: bool        = True,
        pipeline_id:    Optional[str] = None,
        images:         list[str]    = [],
    ) -> Task:
        parent = self.get_task(parent_task_id)
        if parent is None:
            raise ValueError(f"父任务 '{parent_task_id}' 不存在")

        depends_on = [parent_task_id] if wait_for_parent else []

        # 如果未指定 pipeline，继承父任务的 pipeline
        effective_pipeline_id = pipeline_id or parent.pipeline_id or None

        task = await self.submit(
            prompt         = prompt,
            project_name   = project_name,
            auto           = auto,
            images         = images,
            parent_task_id = parent_task_id,
            depends_on     = depends_on,
            pipeline_id    = effective_pipeline_id,
        )
        return task

    def update_pipeline_node_task(self, pipeline_id: str, old_task_id: str, new_task_id: str) -> None:
        """将 pipeline 中的旧 task_id 替换为重试后的新 task_id（用于 retry 流程）。"""
        pipeline = self.get_pipeline(pipeline_id)
        if pipeline is None:
            return
        if old_task_id in pipeline.task_ids:
            idx = pipeline.task_ids.index(old_task_id)
            pipeline.task_ids[idx] = new_task_id
        for nr in pipeline.node_runs:
            if nr.task_id == old_task_id:
                nr.task_id = new_task_id
        pipeline.touch(self.pipelines_dir)
