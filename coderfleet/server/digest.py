"""
digest.py — Daily task digest: stats computation and AI prompt generation.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from coderfleet.server.models import (
    DailyDigest,
    DigestStatus,
    ProjectDigestStats,
    Task,
    TaskStatus,
)

_CODE_BLOCK_RE = re.compile(r"```[\s\S]*?```")
_MULTI_NL_RE   = re.compile(r"\n{3,}")


def _date_of(iso: str) -> str:
    return iso[:10] if iso else ""


def _read_output_excerpt(tasks_dir: Path, task_id: str, acc_type: str, max_chars: int = 350) -> str:
    """
    Read the AI's final result text from a task log and return a clean excerpt.

    Only called for done tasks. Returns "" if log is missing or output is empty.
    Code blocks are replaced with a placeholder to keep the excerpt prose-focused.
    """
    log_path = tasks_dir / f"{task_id}.log"
    if not log_path.exists():
        return ""
    try:
        from coderfleet.server.log_parser import parse_log
        log_text = log_path.read_text(encoding="utf-8", errors="replace")
        text = parse_log(log_text, acc_type).text.strip()
        if not text:
            return ""
        # Strip code blocks — they bloat the prompt without adding narrative value
        text = _CODE_BLOCK_RE.sub("[代码略]", text)
        text = _MULTI_NL_RE.sub("\n", text).strip()
        if len(text) > max_chars:
            # Truncate at a word boundary where possible
            cut = text.rfind(" ", 0, max_chars)
            text = text[: cut if cut > max_chars // 2 else max_chars] + "…"
        return text
    except Exception:
        return ""


def compute_daily_stats(date: str, tasks_dir: Path) -> DailyDigest:
    """
    Build a DailyDigest with stats populated from task files for the given date.

    For completed (done) tasks the AI output excerpt is embedded into the prompt
    line so that build_generate_prompt() can produce a richer narrative without
    any interface changes.
    """
    tasks = Task.load_all(tasks_dir)
    terminal = {TaskStatus.done, TaskStatus.failed, TaskStatus.killed}

    day_tasks = [
        t for t in tasks
        if (t.finished and _date_of(t.finished) == date)
        or (not t.finished and t.status not in terminal and _date_of(t.created) == date)
    ]

    digest = DailyDigest(date=date)
    by_project: dict[str, ProjectDigestStats] = {}

    for t in day_tasks:
        pname = t.project_name or (t.project.split("/")[-1] if t.project else "unknown")
        if pname not in by_project:
            by_project[pname] = ProjectDigestStats(project_name=pname)
        p = by_project[pname]

        if t.status == TaskStatus.done:
            p.done += 1
            digest.total_done += 1
        elif t.status == TaskStatus.failed:
            p.failed += 1
            digest.total_failed += 1
        elif t.status == TaskStatus.killed:
            p.killed += 1
            digest.total_killed += 1

        p.tokens_input  += t.tokens_input
        p.tokens_output += t.tokens_output
        p.cost_usd      += t.cost_usd
        digest.tokens_input  += t.tokens_input
        digest.tokens_output += t.tokens_output
        digest.cost_usd      += t.cost_usd

        short = (t.prompt[:100] + "…") if len(t.prompt) > 100 else t.prompt

        if t.status == TaskStatus.done:
            excerpt = _read_output_excerpt(tasks_dir, t.id, t.type.value)
            if excerpt:
                # Two-line format: input intent + what AI actually produced
                p.prompts.append(f"[done] {short}\n      → {excerpt}")
            else:
                p.prompts.append(f"[done] {short}")
        else:
            p.prompts.append(f"[{t.status.value}] {short}")

    digest.projects = sorted(
        by_project.values(),
        key=lambda x: -(x.done + x.failed + x.killed),
    )
    return digest


def list_active_dates(tasks_dir: Path) -> list[str]:
    """Return dates (YYYY-MM-DD) that have any task activity, newest first."""
    dates: set[str] = set()
    for t in Task.load_all(tasks_dir):
        d = _date_of(t.finished or t.created)
        if d:
            dates.add(d)
    return sorted(dates, reverse=True)


def load_digest(date: str, digests_dir: Path) -> Optional[DailyDigest]:
    p = digests_dir / f"{date}.json"
    if not p.exists():
        return None
    return DailyDigest.load(p)


def build_generate_prompt(digest: DailyDigest) -> str:
    total_tokens = digest.tokens_input + digest.tokens_output
    lines = [
        f"今天是 {digest.date}，以下是 CoderFleet 今日 AI 任务执行数据。",
        "",
        "## 统计汇总",
        f"- 完成任务：{digest.total_done}",
        f"- 失败任务：{digest.total_failed}",
        f"- 终止任务：{digest.total_killed}",
        f"- Token 总消耗：{total_tokens:,}",
        f"- 总费用：${digest.cost_usd:.4f}",
        "",
        "## 各项目详情",
        "",
        "说明：每条完成任务的格式为「[done] 用户指令 → AI 实际输出摘要」，",
        "失败/终止任务只有用户指令。请重点基于「→ AI 输出」来判断实际交付内容。",
    ]
    for p in digest.projects:
        lines.append(f"\n### 项目：{p.project_name}")
        lines.append(f"完成 {p.done} / 失败 {p.failed} / 终止 {p.killed}，费用 ${p.cost_usd:.4f}")
        for entry in p.prompts[:12]:
            lines.append(f"  - {entry}")

    lines += [
        "",
        "---",
        "",
        "请用中文生成一份简洁的今日工作日报，包含以下四个部分：",
        "1. **今日亮点** — 各项目实际完成了哪些有价值的工作（基于 AI 输出摘要归纳，而非仅靠任务名推断）",
        "2. **异常与风险** — 失败或终止的任务，分析可能原因，是否需要人工介入",
        "3. **资源使用** — Token/费用消耗是否合理，有无异常项目",
        "4. **明日建议** — 基于今日进展，给出明天的工作优先级建议",
        "",
        "用 Markdown 格式输出，要言简意赅，每部分 3-5 条要点。",
    ]
    return "\n".join(lines)
