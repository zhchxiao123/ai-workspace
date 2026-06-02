"""
workflow_engine.py — 工作流执行引擎（CoderFleet 完整修改 v2 核心）

本模块为「完整修改工作流」计划 Phase 1 产物。
目标：
- 提供真正的一等公民 WorkflowRun 执行模型（状态机驱动）
- 与现有 Scheduler 解耦或松耦合（账号分配、容器执行仍委托 Scheduler.submit）
- 支持 resume、部分重跑、节点输出（Phase 3）
- 惰性兼容旧 Pipeline 数据

当前状态：骨架 + 接口定义 + 基础 run() 流程（未完全接管调度）

注意：本阶段暂不修改 scheduler.run_template 的主路径，待前端新画布就绪后再逐步切换。
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Optional

from coderfleet.server.models import (
    WorkflowRun,
    NodeExecution,
    WorkflowNodeState,
    WorkflowTemplate,
    TemplateNode,
)


class WorkflowEngine:
    """
    工作流执行引擎。

    职责边界（清晰划分）：
    - 负责 WorkflowRun 生命周期与节点状态机
    - 负责拓扑排序、变量求值（未来）、依赖等待
    - 负责持久化 WorkflowRun
    - **不** 负责：账号选择、容器 docker exec、任务实际执行（委托 Scheduler）
    """

    def __init__(self, workspace_dir: Path, scheduler: "Scheduler"):  # type: ignore  # 避免循环 import
        self.workspace_dir = workspace_dir
        self.runs_dir = workspace_dir / "workflow_runs"   # 新目录，与旧 pipelines/ 并存
        self.scheduler = scheduler

    # ──────────────────────────────────────────────────────────
    # 公共 API（后续将暴露给 main.py）
    # ──────────────────────────────────────────────────────────

    async def run_template(
        self,
        template: WorkflowTemplate,
        input_str: str,
        project_map: dict[str, str],
        default_project: str = "",
    ) -> WorkflowRun:
        """
        从模板启动一次新的 WorkflowRun。
        第一版实现：先创建 Run 记录 + 节点状态，**仍通过 scheduler.submit 实际提交任务**。
        后续版本会逐步把分层提交逻辑迁移到这里。
        """
        run = self._create_run_from_template(template, input_str, project_map)

        # TODO Phase 1 后期：真正按 level 提交并等待 node 状态变更
        # 当前仅占位：直接委托旧逻辑（保持兼容）
        # 真正切换发生在前端新画布 + API 迁移之后
        return run

    def get_run(self, run_id: str) -> Optional[WorkflowRun]:
        path = self.runs_dir / f"{run_id}.json"
        if not path.exists():
            return None
        return WorkflowRun.load(path)

    def list_runs(self) -> list[WorkflowRun]:
        return WorkflowRun.load_all(self.runs_dir)

    async def resume_run(self, run_id: str, from_node_id: Optional[str] = None) -> WorkflowRun:
        """从失败/取消点恢复执行（Phase 2+ 实现）"""
        raise NotImplementedError("resume 功能在 Phase 2 画布支持重跑按钮后实现")

    # ──────────────────────────────────────────────────────────
    # 内部辅助
    # ──────────────────────────────────────────────────────────

    def _create_run_from_template(
        self,
        tpl: WorkflowTemplate,
        input_str: str,
        project_map: dict[str, str],
    ) -> WorkflowRun:
        """根据模板创建初始 WorkflowRun（所有节点 pending/waiting_deps）"""
        run_id = self._new_run_id()

        node_execs: list[NodeExecution] = []
        for node in (tpl.nodes or []):
            ne = NodeExecution(
                node_id=node.node_id,
                name=node.name or node.node_id,
                state=WorkflowNodeState.pending,
                actual_prompt=node.prompt_tpl.replace("{{input}}", input_str),
            )
            node_execs.append(ne)

        run = WorkflowRun(
            id=run_id,
            template_id=tpl.id,
            template_version=1,  # 未来支持版本
            name=f"{tpl.name} · {input_str[:30]}" if input_str else tpl.name,
            trigger_input=input_str,
            status="running",
            node_executions=node_execs,
            project_map=project_map,
        )
        run.save(self.runs_dir)
        return run

    @staticmethod
    def _new_run_id() -> str:
        ts = datetime.now().strftime("%Y%m%d%H%M%S")
        import random
        return f"run-{ts}-{random.randint(1000, 9999)}"

    # 未来方法占位：
    # _topo_sort(...)
    # _resolve_projects(...)
    # _evaluate_prompt(...)   # 支持 {{steps.xxx.outputs.foo}}
    # _mark_node_state(...)
    # _persist_and_notify(...)
