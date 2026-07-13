"""
log_parser.py — Task log output & token usage extraction.

Parses completed task logs to extract:
- Final text output (for {{steps.X.outputs.text}} substitution in workflows)
- Token usage statistics (for usage dashboard)
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

ANSI_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
_HEADER_SEP_RE = re.compile(r"^={10,}\s*$", re.MULTILINE)


def _strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


@dataclass
class TaskOutputData:
    text: str = ""
    tokens_input: int = 0
    tokens_output: int = 0
    cost_usd: float = 0.0


def parse_log(log_text: str, acc_type: str = "claude") -> TaskOutputData:
    """
    Parse a completed task log, returning text output and token usage.

    acc_type should match AccountType.value (e.g. "claude", "codex", "opencode",
    "hermes", "grok", "kimi").  Defaults to "claude" for unknown types.
    """
    result = TaskOutputData()

    if acc_type in ("claude", "codex", "opencode", "grok", "kimi"):
        _parse_jsonl_log(log_text, result, acc_type)

    # Fallback / supplement: plain-text extraction when JSONL gave no text
    if not result.text:
        _parse_text_fallback(log_text, result)

    return result


def extract_task_output(log_text: str, acc_type: str = "claude") -> str:
    """Convenience wrapper — returns only the extracted text."""
    return parse_log(log_text, acc_type).text


# ── per-format parsers ────────────────────────────────────────────────────────

def _parse_jsonl_log(log_text: str, result: TaskOutputData, acc_type: str) -> None:
    text_chunks: list[str] = []

    for line in log_text.splitlines():
        s = line.strip()
        if not s.startswith("{"):
            continue
        try:
            d = json.loads(s)
        except json.JSONDecodeError:
            continue

        t = d.get("type", "")

        if acc_type == "claude":
            _parse_claude_line(d, t, text_chunks, result)
        elif acc_type == "codex":
            _parse_codex_line(d, t, text_chunks, result)
        elif acc_type == "opencode":
            _parse_opencode_line(d, t, text_chunks, result)
        elif acc_type == "grok":
            _parse_grok_line(d, t, text_chunks, result)
        elif acc_type == "kimi":
            _parse_kimi_line(d, text_chunks)

    if not result.text and text_chunks:
        result.text = "".join(text_chunks).strip()


def _parse_claude_line(
    d: dict, t: str, chunks: list[str], result: TaskOutputData
) -> None:
    if t == "result":
        # {"type":"result","result":"...","cost_usd":0.02,"session_id":"..."}
        r = d.get("result", "")
        if r:
            result.text = str(r)
        for key in ("cost_usd", "total_cost_usd"):
            c = d.get(key)
            if c:
                result.cost_usd = float(c)
                break
    elif t == "assistant":
        msg = d.get("message", {})
        for block in msg.get("content", []):
            if isinstance(block, dict) and block.get("type") == "text":
                chunks.append(block["text"])
        usage = msg.get("usage", {})
        if usage:
            result.tokens_input = max(result.tokens_input, int(usage.get("input_tokens", 0)))
            result.tokens_output += int(usage.get("output_tokens", 0))
    elif t == "usage":
        result.tokens_input += int(d.get("input_tokens", 0))
        result.tokens_output += int(d.get("output_tokens", 0))
        c = d.get("cost_usd") or d.get("total_cost_usd", 0)
        if c and not result.cost_usd:
            result.cost_usd = float(c)


def _parse_codex_line(
    d: dict, t: str, chunks: list[str], result: TaskOutputData
) -> None:
    if t == "message" and d.get("role") == "assistant":
        for block in d.get("content", []):
            if isinstance(block, dict) and block.get("type") == "text":
                chunks.append(block["text"])
    elif t in ("usage", "response.usage"):
        result.tokens_input += int(
            d.get("prompt_tokens", 0) or d.get("input_tokens", 0)
        )
        result.tokens_output += int(
            d.get("completion_tokens", 0) or d.get("output_tokens", 0)
        )


def _parse_opencode_line(
    d: dict, t: str, chunks: list[str], result: TaskOutputData
) -> None:
    if d.get("role") == "assistant" or t in ("message", "assistant"):
        content = d.get("content", [])
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    chunks.append(block["text"])
        elif isinstance(content, str):
            chunks.append(content)
    usage = d.get("usage", {})
    if isinstance(usage, dict) and usage:
        result.tokens_input = max(
            result.tokens_input, int(usage.get("input_tokens", 0))
        )
        result.tokens_output += int(usage.get("output_tokens", 0))


def _parse_grok_line(
    d: dict, t: str, chunks: list[str], result: TaskOutputData
) -> None:
    if t == "text_delta":
        chunks.append(d.get("text", ""))
    elif t == "text":
        chunks.append(d.get("data", d.get("text", "")))
    elif t == "end":
        usage = d.get("usage", {})
        if isinstance(usage, dict):
            result.tokens_input = int(usage.get("input_tokens", 0))
            result.tokens_output = int(usage.get("output_tokens", 0))


def _parse_kimi_line(d: dict, chunks: list[str]) -> None:
    if d.get("role") != "assistant":
        return
    content = d.get("content", "")
    if isinstance(content, str):
        chunks.append(content)
    elif isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                chunks.append(str(block.get("text", "")))


# ── plain-text fallback ───────────────────────────────────────────────────────

def _parse_text_fallback(log_text: str, result: TaskOutputData) -> None:
    """
    Extract agent output from the region between the CoderFleet log header
    separator and the first footer separator.
    """
    seps = list(_HEADER_SEP_RE.finditer(log_text))
    if len(seps) >= 2:
        start = seps[0].end()
        end = seps[1].start()
        content = log_text[start:end].strip()
    elif len(seps) == 1:
        content = log_text[seps[0].end():].strip()
    else:
        content = log_text.strip()

    content = _strip_ansi(content)
    # Cap at 8 000 chars to avoid bloating subsequent prompts
    result.text = content[:8000]
