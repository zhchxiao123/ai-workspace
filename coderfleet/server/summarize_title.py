"""
summarize_title.py — 会话自动命名

深度在 SystemLLM 里，这里只剩一层薄薄的域策略：一句 prompt 契约
（只输出标题、不带标点引号）。跟 translate.py 同构，走系统级 LLM，
不占池化账号的容器配额。
"""
from __future__ import annotations

from coderfleet.server.system_llm import SystemLLM

_SYSTEM_PROMPT = (
    "Generate a short title (max 16 characters, same language as the input) that "
    "captures the main topic of the user's message. Output ONLY the title text — "
    "no quotes, no trailing punctuation, no preamble, no explanation."
)


async def summarize_title(llm: SystemLLM, text: str) -> str:
    if not text.strip():
        return ""
    return await llm.complete(
        [{"role": "user", "content": text}],
        system=_SYSTEM_PROMPT,
        max_tokens=64,
    )
