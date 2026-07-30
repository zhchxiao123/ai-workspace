"""
mcp_bridge.py — Intervention 的 MCP 服务器（issue #69 Slice 2）。

单独成文件、且刻意不带 `from __future__ import annotations`：FastMCP 的工具签名
反射（`Tool.from_function` 里的 `issubclass(param.annotation, Context)`）直接对
`inspect.signature()` 给出的标注做 issubclass 判断，不会先解析字符串标注。
main.py 全文件带 PEP 563 的 future import，标注在运行时是字符串，会让这个判断
对任何参数（不只是 Context 类型的）都抛 TypeError——没法在那边直接注册工具，
所以挪到这个不带 future import 的独立模块。
"""
from mcp.server.fastmcp import FastMCP

from coderfleet.server.models import ContinuationTriggerRequest, InterventionQuestion, ScheduleType


def _require_task_id(mcp_server: FastMCP) -> str:
    """从请求头取调用方的 Task 身份——绝不信任客户端传入的任何身份参数。

    同一个 relay 路径被所有账号的容器共用，客户端本来就不可信；这是
    ask_user_question/schedule_continuation 已经在用的同一个模式，新工具
    全部复用，不要各自重新实现一遍。
    """
    ctx = mcp_server.get_context()
    request = ctx.request_context.request
    task_id = request.headers.get("x-coderfleet-task-id", "").strip() if request else ""
    if not task_id:
        raise ValueError("missing X-CoderFleet-Task-Id header")
    return task_id


def _tool_use_id(mcp_server: FastMCP) -> str | None:
    """取 Claude Code 自己的 tool_use.id（`_meta["claudecode/toolUseId"]`），取不到就返回 None。"""
    ctx = mcp_server.get_context()
    meta = ctx.request_context.meta
    return (meta.model_extra or {}).get("claudecode/toolUseId") if meta else None


def build_intervention_mcp(scheduler) -> FastMCP:
    """构建 Intervention 桥接的 MCP server，工具调用绑定到给定的 Scheduler 实例。"""
    # streamable_http_path 默认是 "/mcp"，main.py 又把整个子应用挂在 "/mcp" 前缀下——
    # 两个都不清空的话实际可达路径会变成 "/mcp/mcp" 而不是 "/mcp"。
    mcp_server = FastMCP("coderfleet", streamable_http_path="/")

    @mcp_server.tool(name="ask_user_question")
    async def ask_user_question(questions: list[InterventionQuestion]) -> dict:
        """向人工提问，阻塞直到通过 Web UI/CLI 得到回答，或超过时限自动放行。

        参数类型直接用 InterventionQuestion（跟 CC 原生 AskUserQuestion 同一套
        question/header/multiSelect/options 形状）而不是裸 list[dict]——FastMCP
        会把这个类型转成 Claude 实际看到的工具 JSON schema，让模型知道每道题真正
        能带哪些字段，不是瞎猜一个无约束的 dict。

        认领哪个任务在调用不靠单独的秘密 token——容器能连到这个 bridge 本身就已经
        过了网络隔离层（只有走 gost relay 才能到达，见 compose.py 的转发服务），
        容器自己的 CODERFLEET_TASK_ID 已经是任务粒度的唯一标识，这里复用它。真正
        需要凭证保护的是人工作答那一环（PendingIntervention.token，见
        scheduler.py），因为那条路径面对的是已认证但可能跨任务误触的 Web UI/CLI，
        跟这里"哪个容器在叫这个工具"是两个不同的信任边界。

        已知、接受的信任边界缺口：X-CoderFleet-Task-Id 是个未签名的值，同一个
        relay 路径被所有账号的容器共用，没有什么能拦住一个容器拿别的任务 id 来
        调用这个工具，伪造一条 PendingIntervention 挂到不属于自己的任务上。这不会
        泄露任何真正的凭证——人工作答那一侧的 token 校验完全不受影响——最坏后果是
        误导某个任务显示"有一条待回答的问题"。真要彻底堵住，得给"哪个容器在问"
        也发一份凭证，等于是把这一层再套一次 Slice 1 那种 token 机制；这次先不做，
        当前操作模型（同一个人运营多个自己的账号，不是抵御外部攻击者）下这个代价
        可以接受。
        """
        task_id = _require_task_id(mcp_server)

        # 尽量把这次调用的 CC 自己的 tool_use.id 传下去，这样 Web UI 问答卡片（按
        # tool_use.id 认领）跟 PendingIntervention.tool_call_id 才能对上——否则
        # wait_for_intervention_answer 会自己另铸一个随机 id，人工提交答案时前端
        # 拿到的 pending_intervention.tool_call_id 永远不等于卡片自己的 id，每次
        # 提交都会必现"这个问题已经不再等待回答了"（这是一次真实复现过的 bug，
        # 不是猜测）。Claude Code 通过 MCP 请求的 `_meta` 字段带这个 id，键名固定
        # 是 "claudecode/toolUseId"（含斜杠，不是合法 Python 属性名，取不到就
        # 说明这个 MCP 客户端没有对应字段或者字段名变了，退化成随机 id，至少这次
        # 请求内部依然自洽，只是前端没法精确认领）。
        cc_tool_use_id = _tool_use_id(mcp_server)

        return await scheduler.wait_for_intervention_answer(
            task_id, [q.model_dump() for q in questions], tool_call_id=cc_tool_use_id,
        )

    @mcp_server.tool(name="schedule_continuation")
    async def schedule_continuation(
        prompt: str,
        triggers: list[ContinuationTriggerRequest],
        expires_in_seconds: int = 86400,
    ) -> dict:
        """安排当前 Conversation 的一次性后续任务，并立即返回。

        timer 触发器接受 delay_seconds 或 run_at（二选一）。当前调用不会睡眠或
        保持 Claude 进程；到期后 CoderFleet 创建一个新的 Task，并恢复本会话。
        """
        task_id = _require_task_id(mcp_server)
        tool_use_id = _tool_use_id(mcp_server)
        continuation = await scheduler.arm_continuation(
            source_task_id=task_id,
            prompt=prompt,
            triggers=triggers,
            expires_in_seconds=expires_in_seconds,
            idempotency_key=f"{task_id}:{tool_use_id}" if tool_use_id else None,
        )
        result = continuation.model_dump(mode="json")
        result["webhook_tokens"] = scheduler.take_issued_webhook_tokens(continuation.id)
        return result

    @mcp_server.tool(name="create_schedule")
    async def create_schedule(
        name: str,
        prompt: str,
        schedule_type: ScheduleType,
        time_of_day: str | None = None,
        days_of_week: list[int] | None = None,
        minute_of_hour: int | None = None,
        cron_expr: str | None = None,
        ephemeral_retention: str = "release_on_finish",
        ephemeral_ttl_minutes: int = 120,
    ) -> dict:
        """自助创建一个循环 Schedule（daily/weekly/hourly/cron），并立即生效。

        范围恒等于调用方自己的 project/account，不接受也不信任其他项目/账号；
        不设人工确认闸、不设频率下限——跟人在 Web UI 里建的 Schedule 同等地位。
        只能建在一个 Task 属于某条 Conversation 时，因为改/删权限只认创建它的
        那条 Conversation（见 CONTEXT.md「Schedule 的创建者归属」）。
        """
        task_id = _require_task_id(mcp_server)
        tool_use_id = _tool_use_id(mcp_server)
        sched = await scheduler.create_agent_schedule(
            task_id,
            name=name,
            prompt=prompt,
            schedule_type=schedule_type,
            time_of_day=time_of_day,
            days_of_week=days_of_week,
            minute_of_hour=minute_of_hour,
            cron_expr=cron_expr,
            ephemeral_retention=ephemeral_retention,
            ephemeral_ttl_minutes=ephemeral_ttl_minutes,
            idempotency_key=f"{task_id}:{tool_use_id}" if tool_use_id else None,
        )
        return sched.model_dump(mode="json")

    @mcp_server.tool(name="list_schedules")
    async def list_schedules() -> list[dict]:
        """列出调用方自己项目内的全部 Schedule，不分创建者（人工建的也能看到，但看不到别的项目）。"""
        task_id = _require_task_id(mcp_server)
        return [s.model_dump(mode="json") for s in scheduler.list_schedules_for_task(task_id)]

    @mcp_server.tool(name="get_schedule")
    async def get_schedule(schedule_id: str) -> dict:
        """按 id 查询单条 Schedule，范围同样限定在调用方自己的项目内。"""
        task_id = _require_task_id(mcp_server)
        sched = scheduler.get_schedule_for_task(task_id, schedule_id)
        if sched is None:
            raise ValueError(f"定时计划 '{schedule_id}' 不存在")
        return sched.model_dump(mode="json")

    @mcp_server.tool(name="update_schedule")
    async def update_schedule(
        schedule_id: str,
        name: str | None = None,
        prompt: str | None = None,
        schedule_type: ScheduleType | None = None,
        time_of_day: str | None = None,
        days_of_week: list[int] | None = None,
        minute_of_hour: int | None = None,
        cron_expr: str | None = None,
        ephemeral_retention: str | None = None,
        ephemeral_ttl_minutes: int | None = None,
    ) -> dict:
        """更新一条自己创建的 Schedule；只能改自己（当前 Conversation）建的那些。

        只暴露安全字段——project_name/account/created_by 这类归属字段不在
        这个签名里，agent 没有任何途径通过这个工具改到自己的项目/账号范围之外。
        """
        task_id = _require_task_id(mcp_server)
        updates = {
            k: v for k, v in {
                "name": name, "prompt": prompt, "schedule_type": schedule_type,
                "time_of_day": time_of_day, "days_of_week": days_of_week,
                "minute_of_hour": minute_of_hour, "cron_expr": cron_expr,
                "ephemeral_retention": ephemeral_retention,
                "ephemeral_ttl_minutes": ephemeral_ttl_minutes,
            }.items() if v is not None
        }
        sched = scheduler.update_agent_schedule(task_id, schedule_id, updates)
        return sched.model_dump(mode="json")

    @mcp_server.tool(name="cancel_schedule")
    async def cancel_schedule(schedule_id: str) -> dict:
        """取消（删除）一条自己创建的 Schedule；只能取消自己建的那些。"""
        task_id = _require_task_id(mcp_server)
        scheduler.cancel_agent_schedule(task_id, schedule_id)
        return {"cancelled": True, "id": schedule_id}

    @mcp_server.tool(name="toggle_schedule")
    async def toggle_schedule(schedule_id: str) -> dict:
        """切换一条自己创建的 Schedule 的启用/暂停状态；只能操作自己建的那些。"""
        task_id = _require_task_id(mcp_server)
        sched = scheduler.toggle_agent_schedule(task_id, schedule_id)
        return sched.model_dump(mode="json")

    return mcp_server
