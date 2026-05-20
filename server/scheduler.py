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
import shlex
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional

import docker_mgr
from models import (
    Account,
    AccountAuth,
    AccountProxy,
    AccountResponse,
    AccountType,
    Conversation,
    ConversationStatus,
    Project,
    Task,
    TaskStatus,
)


class Scheduler:
    def __init__(self, workspace_dir: Path):
        self.workspace_dir  = workspace_dir
        self.accounts_conf  = workspace_dir / "accounts.conf"
        self.projects_conf  = workspace_dir / "projects.conf"
        self.tasks_dir      = workspace_dir / "tasks"
        self.conversations_dir = workspace_dir / "conversations"
        # task_id → asyncio.Task（后台运行的协程）
        self._running: dict[str, asyncio.Task] = {}

    @staticmethod
    def task_process_marker(task_id: str) -> str:
        return f"aicm-task-{task_id}"

    @staticmethod
    def build_cli_command(
        acc_type: AccountType,
        prompt: str,
        auto: bool,
        task_id: str,
        native_session_id: str = "",
        container_workdir: str = "",
    ) -> str:
        """
        构建在容器内执行的 CLI 命令。
        始终开启 JSON 输出（--output-format stream-json / --json）以便提取 native_session_id。
        """
        escaped_prompt = shlex.quote(prompt)
        marker = shlex.quote(Scheduler.task_process_marker(task_id))
        task_env = shlex.quote(task_id)

        if acc_type == AccountType.claude:
            permission = "--dangerously-skip-permissions" if auto else "--permission-mode acceptEdits"
            # 始终使用流式 JSON 输出以捕获 session_id
            output_format = " --output-format stream-json --verbose"
            resume = f" --resume {shlex.quote(native_session_id)}" if native_session_id else ""
            cli_cmd = f"claude -p {permission}{output_format}{resume} {escaped_prompt}"
        else:
            sandbox = "danger-full-access" if auto else "workspace-write"
            # 始终使用 --json 以捕获 thread_id
            if native_session_id:
                # codex exec resume <session_id> <prompt> --json
                # resume 子命令不支持 --sandbox，使用 --dangerously-bypass-approvals-and-sandbox
                danger_flag = " --dangerously-bypass-approvals-and-sandbox" if auto else ""
                cli_cmd = (
                    f"codex exec resume --json{danger_flag} "
                    f"{shlex.quote(native_session_id)} {escaped_prompt}"
                )
            else:
                cli_cmd = f"codex exec --json --sandbox {sandbox} {escaped_prompt}"

        inner_cmd = f"AICM_TASK_ID={task_env} exec -a {marker} {cli_cmd}"
        if container_workdir:
            inner_cmd = f"cd {shlex.quote(container_workdir)} && {inner_cmd}"

        task_log  = f"/workspace/.aicm-tasks/{task_id}.log"
        task_exit = f"/workspace/.aicm-tasks/{task_id}.exit"
        # 用子 shell ( ... ) 包裹 inner_cmd：exec -a 替换的是子 shell 进程，
        # 外层 bash 在子 shell 退出后仍可执行 echo $? 写入 exit 文件。
        # 若不加括号，exec -a 会直接替换外层 bash，分号后的 echo $? 永远不会执行。
        wrapper_body = (
            f"( {inner_cmd} ) >> {shlex.quote(task_log)} 2>&1"
            f"; echo $? > {shlex.quote(task_exit)}"
        )
        return (
            f"mkdir -p /workspace/.aicm-tasks"
            f" && setsid bash -c {shlex.quote(wrapper_body)} &"
        )

    @staticmethod
    def build_usage_status_command(acc_type: AccountType) -> str:
        if acc_type == AccountType.codex:
            return "aicm-usage-status codex 2>&1"
        return ""

    @staticmethod
    def extract_native_session_id(acc_type: AccountType, text: str) -> str:
        for line in text.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if acc_type == AccountType.codex:
                if data.get("type") == "thread.started" and data.get("thread_id"):
                    return str(data["thread_id"])
            elif data.get("session_id"):
                return str(data["session_id"])
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
            if auth == AccountAuth.env and acc_type != AccountType.claude:
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
                ))

        return projects

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
        找一个满足条件的空闲账号：
        - 类型匹配（可选）
        - 项目路径匹配（可选，规范化后比较）
        - 至少有一个项目容器在线
        - 没有 running 任务占用
        """
        busy = self.get_busy_accounts()

        project_match = self.find_project_for_path(prefer_project) if prefer_project else None

        for acc in self.get_accounts():
            if prefer_type and acc.type != prefer_type:
                continue
            if project_match:
                if acc.name != project_match.account:
                    continue
            if acc.name in busy:
                continue
            # Check if any project container for this account is running
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

    def find_project_for_path(self, project: Optional[str]) -> Optional[Project]:
        if not project:
            return None
        matching = [
            p for p in self.get_projects()
            if self._path_under_root(p.path, project)
        ]
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

    @staticmethod
    def new_conversation_id() -> str:
        ts   = datetime.now().strftime("%Y%m%d%H%M%S")
        rand = random.randint(0, 9999)
        return f"conv-{ts}-{rand:04d}"

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
    ) -> Task:
        """
        提交任务，异步在后台执行，立即返回 Task 对象。
        调用方可以通过 task.id 跟踪进度。
        """
        conversation: Optional[Conversation] = None

        if conversation_id:
            conversation = self.get_conversation(conversation_id)
            if conversation is None:
                raise ValueError(f"任务链 '{conversation_id}' 不存在")
            self.ensure_conversation_available(conversation)
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
        if account_name:
            acc = next((a for a in self.get_accounts() if a.name == account_name), None)
            if acc is None:
                raise ValueError(f"账号 '{account_name}' 不存在")
            if prefer_type and acc.type != prefer_type:
                raise ValueError(
                    f"账号 '{account_name}' 类型为 {acc.type.value}，与筛选类型 {prefer_type.value} 不一致"
                )
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
                    f"没有匹配的空闲账号{('（' + hint_str + '）') if hint_str else ''}"
                )

        task_project = self.resolve_task_project(acc, prefer_project)
        selected_project = selected_project or self.find_project_for_path(task_project)
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
        )
        task.save(self.tasks_dir)

        # 写日志头
        self._write_log_header(log_path, task, acc, container_workdir, container_name)

        # 异步后台执行
        bg = asyncio.create_task(
            self._run(task, acc, log_path, auto, conversation, container_workdir, container_name),
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
        project = self.find_project_for_path(task.project)
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
            f.write("=== AICM Task Log ===\n")
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
            await self._append_usage_status(log_path, acc, container_name)
            self._append_log_footer(log_path, "done")
        else:
            task.update_status(TaskStatus.failed, self.tasks_dir)
            await self._append_usage_status(log_path, acc, container_name)
            self._append_log_footer(log_path, f"failed (exit={rc})")

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
            )

            proc = await asyncio.create_subprocess_exec(
                "docker", "exec", container_name, "bash", "-c", cmd,
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
            self._append_log_footer(log_path, "killed")
            return
        except Exception as e:
            task.update_status(TaskStatus.failed, self.tasks_dir)
            await self._append_usage_status(log_path, acc, container_name)
            self._append_log_footer(log_path, f"failed: {e}")
            return
        finally:
            self._running.pop(task.id, None)
            self._cleanup_container_task_files(task)

    async def _reattach(self, task: Task) -> None:
        """重新 attach 到一个在 Python 重启后仍存活的容器进程，继续跟踪其日志。"""
        log_path = self.get_log_path(task.id)

        acc = next((a for a in self.get_accounts() if a.name == task.account), None)
        if acc is None:
            task.update_status(TaskStatus.failed, self.tasks_dir)
            self._append_log_footer(log_path, "failed: account not found on reattach")
            return

        project_root = self._get_project_root(task)
        if project_root is None:
            task.update_status(TaskStatus.failed, self.tasks_dir)
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
            self._append_log_footer(log_path, "killed")
            return
        except Exception as e:
            task.update_status(TaskStatus.failed, self.tasks_dir)
            self._append_log_footer(log_path, f"failed during reattach: {e}")
            return
        finally:
            self._running.pop(task.id, None)
            self._cleanup_container_task_files(task)

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

    def _get_task_container(self, task: Task) -> Optional[str]:
        """通过任务记录的 project 路径找到对应项目，推导容器名。"""
        project = self.find_project_for_path(task.project)
        if project is None:
            return None
        return project.container_name(task.type)

    def _get_project_root(self, task: Task) -> Optional[Path]:
        """返回任务所属项目在宿主机的根目录（/workspace 在容器内挂载的目标）。"""
        project = self.find_project_for_path(task.project)
        if project is None:
            return None
        return Path(project.path)

    @staticmethod
    def _host_task_log(project_root: Path, task_id: str) -> Path:
        return project_root / ".aicm-tasks" / f"{task_id}.log"

    @staticmethod
    def _host_task_exit(project_root: Path, task_id: str) -> Path:
        return project_root / ".aicm-tasks" / f"{task_id}.exit"

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
            self._append_log_footer(self.get_log_path(task.id), result)
            self._cleanup_container_task_files(task)
            reconciled += 1

        return reconciled

    # ── 终止任务 ──────────────────────────────────────────

    async def kill_task(self, task_id: str) -> Task:
        task = self.get_task(task_id)
        if task is None:
            raise ValueError(f"任务 '{task_id}' 不存在")
        if task.status != TaskStatus.running:
            raise RuntimeError(f"任务状态为 '{task.status.value}'，只能终止 running 状态的任务")

        # 先更新状态防止并发写入
        task.update_status(TaskStatus.killed, self.tasks_dir)

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
