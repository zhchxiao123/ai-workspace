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
    AccountTypeSpec,
    CostDelta,
    CostFirstSeen,
    CostTotal,
    EventsFn,
    FinalText,
    OutputEvent,
    TextChunk,
    ToolIntent,
    ToolOutcome,
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

def _classifier_for(acc_type: str) -> tuple[AccountTypeSpec | None, EventsFn | None]:
    """
    Look up an account type's spec and its line classifier, tolerating misses.

    Deliberately ACCOUNT_TYPES.get(acc_type), not account_type_registry's
    get_spec() -- get_spec() raises KeyError for an unrecognized acc_type,
    which would turn an unknown/legacy acc_type string into a hard crash
    instead of the graceful fall-through parse_log() has always had. Callers
    pass acc_type straight through from a Task record, which can predate a
    type's removal or simply be malformed input.

    A `None` classifier means the type makes no per-line claim at all (hermes:
    plain text, not JSONL).
    """
    spec = ACCOUNT_TYPES.get(acc_type)
    return spec, (spec.parse_output_events if spec else None)


def _iter_json_lines(log_text: str):
    """
    Yield each JSON-object line of a task log, skipping everything else.

    Shared by the fold and the coverage detector so the two cannot disagree
    about which lines of a log even count — they answer questions about the
    same population or the detector's coverage claim is about a different log
    than the one that was parsed.
    """
    for line in log_text.splitlines():
        s = line.strip()
        if not s.startswith("{"):
            continue
        try:
            d = json.loads(s)
        except json.JSONDecodeError:
            continue
        if isinstance(d, dict):
            yield d


def _parse_jsonl_log(log_text: str, result: TaskOutputData, acc_type: str) -> None:
    _, events_fn = _classifier_for(acc_type)
    if events_fn is None:
        return

    text_chunks: list[str] = []
    for d in _iter_json_lines(log_text):
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
    elif isinstance(event, (ToolIntent, ToolOutcome)):
        # Deliberate no-op, spelled out rather than left to fall off the end of
        # the chain (issue #74). TaskOutputData models a finished task's answer
        # (text) and its price (tokens/cost); a tool call is neither, and
        # folding one into `text` would corrupt the value workflows substitute
        # into {{steps.X.outputs.text}}. Tool events exist for consumers that
        # read the event stream directly — today `detect_unrecognized_shapes`.
        pass


# ── coverage detector (issue #74) ─────────────────────────────────────────────

@dataclass(frozen=True)
class ShapeCoverage:
    """
    One coverage verdict: which of `acc_type`'s line shapes went unclassified.

    Carries `acc_type` rather than just the shape counts because shape names
    collide across types (`text`, `message_end`, `assistant` all appear in more
    than one format) — a bare shape name is not enough to act on. `__str__` is
    the form meant for a failure message.
    """
    acc_type: str
    unrecognized: dict[str, int]  # shape name → occurrences, all of them silent

    def __str__(self) -> str:
        if not self.unrecognized:
            return f"{self.acc_type}: all line shapes accounted for"
        shapes = ", ".join(
            f"{shape!r} ×{count}" for shape, count in sorted(self.unrecognized.items())
        )
        return f"{self.acc_type}: unclassified line shapes: {shapes}"


def detect_unrecognized_shapes(log_text: str, acc_type: str) -> ShapeCoverage:
    """
    Report line shapes this account type's classifier never turned into events.

    `unrecognized` holds every shape that (a) appeared at least once,
    (b) produced zero OutputEvents on *every* one of its occurrences, and
    (c) is not declared in the spec's `silent_shapes`. Empty means the log is
    fully accounted for.

    This is the real reader of the tool-call events: before they existed, most
    lines in a real log legitimately produced nothing, so "produced nothing" was
    meaningless noise. Now that requests and results classify too, a shape going
    quiet across a whole log is a usable signal that a CLI changed its output
    format — the failure mode behind both #69 and #70, each of which was only
    caught by eye.

    Silence is judged per shape across the whole log, not per line, because one
    shape can legitimately be silent on some lines and not others (pi emits
    `message_end` for user turns as well as assistant ones). Detection is
    therefore at whatever granularity the type's `shape_of` gives; a type that
    needs finer resolution sharpens its own `shape_of`.

    Advisory and read-only: nothing here feeds TaskOutputData, and this is
    deliberately not wired into the scheduler or server — picking an
    enforcement policy (warn / fail / rate-limit) is a separate decision. Its
    consumer today is the test suite. Types with no classifier (hermes, which
    is plain text) make no coverage claim, and an unknown acc_type falls
    through the same way parse_log() does rather than raising; both report
    nothing.
    """
    spec, events_fn = _classifier_for(acc_type)
    if spec is None or events_fn is None:
        return ShapeCoverage(acc_type, {})

    seen: dict[str, int] = {}
    classified: set[str] = set()
    for d in _iter_json_lines(log_text):
        # `shape_of` is required to be total over dicts (it only reads fields
        # and stringifies), so it is called unguarded — a shape name is needed
        # to report anything at all, including a failure.
        shape = spec.shape_of(d)
        try:
            events = events_fn(d)
        except Exception:
            # A classifier that blew up did not classify the line, and a line
            # it cannot survive is precisely the drift this detector exists to
            # surface (a CLI retyping a field: object → string, int → string).
            # Swallowing keeps the documented "does not raise" contract, and
            # the line is still counted against its shape below — so the
            # report names it instead of the caller dying on a stack trace.
            # Deliberately bare: every exception a classifier can raise means
            # the same thing here.
            events = []
        seen[shape] = seen.get(shape, 0) + 1
        if events:
            classified.add(shape)

    return ShapeCoverage(acc_type, {
        shape: count
        for shape, count in seen.items()
        if shape not in classified and shape not in spec.silent_shapes
    })


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
