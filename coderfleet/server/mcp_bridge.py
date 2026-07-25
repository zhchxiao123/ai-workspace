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

from coderfleet.server.models import InterventionQuestion


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
        ctx = mcp_server.get_context()
        request = ctx.request_context.request
        task_id = request.headers.get("x-coderfleet-task-id", "").strip() if request else ""
        if not task_id:
            raise ValueError("missing X-CoderFleet-Task-Id header")
        return await scheduler.wait_for_intervention_answer(
            task_id, [q.model_dump() for q in questions],
        )

    return mcp_server
