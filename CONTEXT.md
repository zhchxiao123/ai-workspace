# CoderFleet 概念地图（CONTEXT.md）

> 本文档固化 CoderFleet 的**目标心智模型**，作为编排（orchestration）、看板（board）等一切后续改造的术语与对齐基准。当代码与本文冲突时，以本文描述的目标模型为方向，逐步收敛。
>
> 关联：`docs/agents/domain.md`（单一 CONTEXT.md 惯例）、`docs/adr/`（架构决策）。

## 一句话模型

**1 个执行原子 + 2 种编排 + 1 个覆盖层 + 若干镜头。**

```
Account ──┐
          ▼
        Project ──────────────► Task            ← 唯一执行原子（1 次容器内 CLI 调用）
                                 ▲
        编排：如何把 Task 串起来 │
        ├─ Conversation  串行链  │  --resume 带上下文，面向交互 / 手动
        └─ Workflow      DAG     │  声明式模板 → 运行，面向自动化
                                 │
        覆盖层：人如何看进度      │
        └─ Board  纯视图 ── 指向 ─┘  卡片贴在「一条会话」或「一个工作流运行」上，
                                     自身不执行、不持有任务
        镜头：同一批 Task 的不同看法
        ├─ Tasks     原始列表 / 日志 / kill
        ├─ Digest    按天汇总 + AI 摘要
        └─ Schedules 定时触发器（触发一个 Task 或一个 Workflow）
```

## 概念定义与「job to be done」

| 概念 | 一句话职责 | 不做什么 |
|---|---|---|
| **Account** | 持有第三方 CLI（claude/codex…）的认证与代理配置 | 不直接执行任务 |
| **Project** | 定义一份工作目录 + 容器环境，是「一个容器」的单位 | 不定义多步流程 |
| **Task** | **唯一执行原子**：一次容器内 CLI 调用，有独立状态机 | 不自带编排语义 |
| **Conversation** | 把多个 Task 串成**串行链**，靠 `native_session_id` + `--resume` 复用上下文 | 不做并行 / 条件分支 |
| **Workflow** | 声明式 **DAG 编排**：`WorkflowTemplate`（蓝图）→ `WorkflowRun`（运行实例，带节点状态机、条件分支、审批、重试） | 不承担「人工进度看板」职责 |
| **Board** | **纯覆盖层**：卡片指向一条 Conversation 或一个 Workflow 运行，用于人工进度跟踪 | 不执行、不直接持有 Task 列表 |
| **Schedule** | 定时**触发器**，到点触发一个 Task 或一个 Workflow | 自身不执行，代理给 Task/Workflow |
| **Continuation** | Conversation 的一次性后续触发记录；timer / webhook 中第一个信号追加一个 Task | 自身不执行，也不是长期重复计划 |
| **Digest** | 按天对 Task 历史做统计 + AI 摘要 | 只读镜头，不参与执行 |

## 「该用哪个」决策指引

- **要执行一件事** → 提交一个 **Task**。
- **要多步、且后一步依赖前一步的上下文（交互式）** → 用 **Conversation**（串行链）。
- **要多步、有分支/并行/审批/重试（自动化）** → 用 **Workflow**（模板 + 运行）。
- **要跟踪一批工作的人工进度** → 用 **Board**，让卡片指向对应的会话或工作流运行。
- **要定时跑** → 用 **Schedule**，目标选 Task 或 Workflow。人工经 Web UI/CLI 配置，或运行中的 agent 经 MCP `create_schedule` 自助创建。
- **要在当前会话稍后或等外部事件后继续（只等一次）** → 用 **Continuation**；它向原 Conversation 追加 Task，到期或触发后即终止，不是重复计划。
- **要让运行中的 agent 自己建一个长期循环检查（每天/每小时/cron）** → 用 **Schedule**，不要把 **Continuation** 拿来循环用——Continuation 的 fired 是终态，硬套循环等于每次都要重新武装一次，语义和生命周期都对不上。

## Schedule 的创建者归属

`Schedule` 有创建者维度：`created_by: human | agent`。人工经 Web UI/CLI 配置的是 `human`；运行中的 agent 经 MCP `create_schedule` 自助创建的是 `agent`，并额外记一条 `created_by_conversation_id`（哪条 Conversation 建的）。

- **生效方式对等**：agent 创建的 Schedule 跟人工创建的一样，保存即 `enabled=True`、立刻生效——不因为创建者是 agent 就多一道人工确认闸，也不设频率下限；这是权衡后的决定，见 `docs/adr/0001-agent-self-service-schedules.md`。
- **读写权限不对等**：项目内所有 Schedule 对 agent 可读（list/get，不分创建者）；但改/删/toggle 只能由 `created_by_conversation_id` 指向的那条 Conversation 自己发起——碰不到人工建的，也碰不到别的会话建的。
- **范围恒等于调用方自己的 project/account**：`create_schedule` 不接受调用方以外的 `project_name`/`account`，服务端从请求的 Task 身份派生，不信任客户端传入值（跟 `ask_user_question`/`schedule_continuation` 从 `X-CoderFleet-Task-Id` 取身份是同一模式）。

## 退役与收敛方向

- **`Pipeline`（工作流 v1）作为面向用户的概念退役。** 统一到 `Workflow` / `WorkflowRun`（v2，一等公民节点状态机）。历史 `Pipeline` 数据迁移为 `WorkflowRun`，`/api/pipelines*` 与「手动新建流水线」入口下线。（见 issue #17）
- **前端一切工作流视图以 `WorkflowRun.node_executions[].state` 为准**，不再依赖 `task.status` 或旧 `node_runs` 推断节点状态。（见 issue #10 / #13 / #14）
- **Board 卡片收敛为单一引用**（会话 XOR 工作流运行），不再直接持有 `task_ids[]`。（见 issue #16）
- **每个功能 CLI 与 Web UI 能力对等** —— boards / workflows / schedules / digest 均需补齐 CLI。（见 issue #12 / #18 / #19）

## 节点状态机（WorkflowRun）

`WorkflowNodeState`：`pending → waiting_deps → running → succeeded | failed | skipped | cancelled | waiting_approval`

- `skipped`：未命中条件分支的下游节点。
- `waiting_approval`：人工审批节点等待用户批准。

这两个状态在 UI 上必须与「普通待执行」有明确视觉区分（见 issue #13）。
