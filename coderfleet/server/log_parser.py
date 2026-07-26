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

from coderfleet.account_type_registry import (
    ACCOUNT_TYPES,
    CostDelta,
    CostFirstSeen,
    CostTotal,
    FinalText,
    OutputEvent,
    TextChunk,
    UsageDelta,
    UsageProgress,
    UsageTotal,
)

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
    "hermes", "grok", "kimi", "pi"). Unknown types, and types with no registered
    `parse_output_events` (currently only "hermes", which is plain text, not
    JSONL), fall straight through to the plain-text fallback below.
    """
    result = TaskOutputData()

    _parse_jsonl_log(log_text, result, acc_type)

    # Fallback / supplement: plain-text extraction when JSONL gave no text
    if not result.text:
        _parse_text_fallback(log_text, result)

    return result


def extract_task_output(log_text: str, acc_type: str = "claude") -> str:
    """Convenience wrapper — returns only the extracted text."""
    return parse_log(log_text, acc_type).text


# ── per-format parsing (account_type_registry.py-driven) ───────────────────────
#
# Each account type's `AccountTypeSpec.parse_output_events` (a pure function
# owned by account_type_registry.py) classifies one decoded JSON line into
# normalized OutputEvents; this module owns only the generic fold from events
# into TaskOutputData, via `_apply_event`'s one fixed rule per event kind.
# Adding or fixing an account type's output parsing therefore means editing
# exactly one function in account_type_registry.py, not this dispatch table
# (see issue #70 — this replaces six independently-drifting
# `_parse_<type>_line` functions that did not derive from ACCOUNT_TYPES).

def _parse_jsonl_log(log_text: str, result: TaskOutputData, acc_type: str) -> None:
    # Deliberately ACCOUNT_TYPES.get(acc_type), not account_type_registry's
    # get_spec() -- get_spec() raises KeyError for an unrecognized acc_type,
    # which would turn an unknown/legacy acc_type string into a hard crash
    # here instead of the graceful fall-through to _parse_text_fallback that
    # parse_log() has always had. This function's caller passes acc_type
    # straight through from a Task record, which can predate a type's
    # removal or simply be malformed input.
    spec = ACCOUNT_TYPES.get(acc_type)
    events_fn = spec.parse_output_events if spec else None
    if events_fn is None:
        return

    text_chunks: list[str] = []
    for line in log_text.splitlines():
        s = line.strip()
        if not s.startswith("{"):
            continue
        try:
            d = json.loads(s)
        except json.JSONDecodeError:
            continue

        for event in events_fn(d):
            _apply_event(event, result, text_chunks)

    if not result.text and text_chunks:
        result.text = "".join(text_chunks).strip()


def _apply_event(event: OutputEvent, result: TaskOutputData, chunks: list[str]) -> None:
    """
    Fold one normalized OutputEvent into the running (result, chunks) accumulator.

    Each event kind has exactly one fixed combination rule, applied the same
    way regardless of which account type produced the event — see
    account_type_registry.py's OutputEvent section for why these are the only
    combination rules observed across the real formats today.
    """
    if isinstance(event, TextChunk):
        chunks.append(event.text)
    elif isinstance(event, FinalText):
        result.text = event.text
    elif isinstance(event, UsageDelta):
        result.tokens_input += event.input_tokens
        result.tokens_output += event.output_tokens
    elif isinstance(event, UsageProgress):
        result.tokens_input = max(result.tokens_input, event.input_tokens)
        result.tokens_output += event.output_tokens
    elif isinstance(event, UsageTotal):
        if event.input_tokens is not None:
            result.tokens_input = event.input_tokens
        if event.output_tokens is not None:
            result.tokens_output = event.output_tokens
    elif isinstance(event, CostDelta):
        result.cost_usd += event.cost_usd
    elif isinstance(event, CostFirstSeen):
        if not result.cost_usd:
            result.cost_usd = event.cost_usd
    elif isinstance(event, CostTotal):
        result.cost_usd = event.cost_usd


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
