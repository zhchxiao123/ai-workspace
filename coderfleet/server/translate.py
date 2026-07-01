"""
translate.py — 气泡翻译

深度在 SystemLLM 里，这里只剩一层薄薄的域策略：一句 prompt 契约
（只输出译文、保留 markdown）。它从来就不深，就该是一个函数。
"""
from __future__ import annotations

from coderfleet.server.system_llm import SystemLLM

_SYSTEM_PROMPT = (
    "You are a translation engine. Translate the user's message into {target}. "
    "Output ONLY the translation — no preamble, no explanation, no quotes around it. "
    "Preserve all Markdown structure, code blocks, inline code, links and formatting "
    "exactly. Leave code, identifiers and URLs untranslated."
)


async def translate_text(llm: SystemLLM, text: str, target: str = "简体中文") -> str:
    if not text.strip():
        return ""
    return await llm.complete(
        [{"role": "user", "content": text}],
        system=_SYSTEM_PROMPT.format(target=target),
        max_tokens=4096,
    )
