"""
log_parser.py — Task log output & token usage extraction.

Parses completed task logs to extract:
- Final text output (for {{steps.X.outputs.text}} substitution in workflows)
- Token usage statistics (for usage dashboard)

Also hosts `split_complete_lines`, a tiny line-boundary-safe helper shared by
every place that tails a growing log file in byte chunks (scheduler.py's
host-log → log_path copy, main.py's SSE tail) — see its own docstring.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

ANSI_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
_HEADER_SEP_RE = re.compile(r"^={10,}\s*$", re.MULTILINE)


def _strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


def split_complete_lines(buf: bytes) -> tuple[bytes, bytes]:
    """
    Split `buf` at its last newline into (complete, pending).

    `complete` is everything up to and including the last b"\\n" — safe to
    persist/emit right now. `pending` is the trailing partial line (no
    newline yet) that must be prepended to the next chunk before re-checking.

    Exists because polling a file that's still being appended to (a
    subprocess's stdout, mid-write) can observe a byte range that ends
    partway through a line or even mid-UTF-8-codepoint. A caller that
    persists/emits raw byte deltas without this check will, on an unlucky
    poll tick, permanently tear one logical JSONL record into two unrelated
    lines — every downstream JSON.parse of that record then fails and the
    line degrades to a raw/garbled display. Larger individual lines (e.g. a
    bigger `tool_use_result` after a CLI upgrade) make an unlucky tick more
    likely simply because a big line takes longer to write, giving more polls
    a chance to land mid-line — this isn't a new class of bug, just one that
    got easier to trigger.

    Callers must flush any remaining `pending` once the source is known to
    have stopped growing (process exited) — otherwise a final line that
    never gets an appended newline is silently dropped.
    """
    last_nl = buf.rfind(b"\n")
    if last_nl < 0:
        return b"", buf
    return buf[: last_nl + 1], buf[last_nl + 1:]


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

    if acc_type in ("claude", "codex", "opencode", "grok", "kimi", "pi"):
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
        elif acc_type == "pi":
            _parse_pi_line(d, t, text_chunks, result)

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


def _parse_pi_line(
    d: dict, t: str, chunks: list[str], result: TaskOutputData
) -> None:
    # `--mode json` emits one `message_end` per completed message; the final
    # answer is whichever assistant message_end(s) land last (accumulated the
    # same way opencode/kimi are, since pi has no single dedicated "result"
    # event like claude's `type=="result"`). `agent_end` re-lists the same
    # messages, so it's intentionally not parsed here to avoid double-counting.
    if t != "message_end":
        return
    msg = d.get("message", {})
    if msg.get("role") != "assistant":
        return
    for block in msg.get("content", []):
        if isinstance(block, dict) and block.get("type") == "text":
            chunks.append(block.get("text", ""))
    usage = msg.get("usage", {})
    if isinstance(usage, dict) and usage:
        result.tokens_input = max(result.tokens_input, int(usage.get("input", 0)))
        result.tokens_output += int(usage.get("output", 0))
        cost = usage.get("cost", {})
        if isinstance(cost, dict) and cost.get("total"):
            result.cost_usd = float(cost["total"])


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


# ── light-mode log stripping (for the Web UI's history viewer) ────────────────

# JSON fields that carry raw per-event metadata Claude Code's stream-json format
# duplicates alongside the actual displayed content (e.g. `tool_use_result` mirrors
# the same tool output already present in `message.content[].content`, but as full
# unprocessed structured data). renderer.js never reads these — on real logs they
# routinely account for >95% of the file's bytes, so stripping them before sending
# a log to the Web UI cuts network + JSON.parse cost dramatically without touching
# anything actually rendered. Add a field here only after confirming (grep the whole
# `server/static/js/` tree) that no renderer path reads it.
_UNUSED_RENDER_FIELDS = ("tool_use_result",)


def strip_unused_render_fields(log_text: str) -> str:
    """
    Drop `_UNUSED_RENDER_FIELDS` from each JSON-object line of a task log.

    Best-effort and line-local: any line that isn't a single JSON object, or that
    fails to parse, is passed through unchanged. This must never be used for the
    CLI's raw `task logs` output or anything a user expects byte-for-byte —
    only for the Web UI's display-only fetches (chat/log-modal/workflow viewers).
    """
    if not any(f'"{f}"' in log_text for f in _UNUSED_RENDER_FIELDS):
        return log_text

    out_lines = []
    for line in log_text.split("\n"):
        if not any(f'"{f}"' in line for f in _UNUSED_RENDER_FIELDS):
            out_lines.append(line)
            continue
        try:
            d = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            out_lines.append(line)
            continue
        if not isinstance(d, dict):
            out_lines.append(line)
            continue
        changed = False
        for field in _UNUSED_RENDER_FIELDS:
            if field in d:
                del d[field]
                changed = True
        out_lines.append(json.dumps(d, ensure_ascii=False) if changed else line)
    return "\n".join(out_lines)
