#!/usr/bin/env python3
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import time
from typing import Any


ANSI_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
STATUS_LINE_RE = re.compile(
    r"(5h|five|weekly|week|limit|left|remaining|usage|quota|reset|used|剩余|用量|限制|重置)",
    re.IGNORECASE,
)


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


def unique_lines(lines: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for line in lines:
        if line not in seen:
            seen.add(line)
            result.append(line)
    return result


def clean_status_line(line: str) -> str:
    cleaned = line.strip().strip("│").strip()
    cleaned = re.sub(r"\[[█░▒▓#=\-\s]+\]\s*", "", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    cleaned = re.sub(r"^([A-Za-z0-9 ]+ limit:)\s+", r"\1 ", cleaned)
    return cleaned


def extract_codex_usage_summary(text: str) -> str:
    cleaned_text = strip_ansi(text)
    matched = [
        clean_status_line(line)
        for line in cleaned_text.splitlines()
        if re.search(r"\b(5h|Weekly) limit:\s+", line, re.IGNORECASE)
    ]
    matched = [line for line in matched if line]
    return "\n".join(unique_lines(matched))


def check_codex_login() -> bool:
    try:
        result = subprocess.run(
            ["codex", "login", "status"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=20,
            check=False,
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def read_available(child: Any, pexpect_module: Any, size: int = 20000, timeout: int = 3) -> str:
    try:
        return child.read_nonblocking(size=size, timeout=timeout)
    except pexpect_module.TIMEOUT:
        return ""


def collect_codex_status() -> str:
    if shutil.which("codex") is None:
        return "Codex CLI 不存在，跳过用量检查"
    if not check_codex_login():
        return "Codex 未登录或凭证不可用，跳过用量检查"

    import pexpect

    child = pexpect.spawn(
        "codex",
        encoding="utf-8",
        timeout=25,
        dimensions=(40, 160),
    )
    buf = ""

    try:
        time.sleep(2)
        buf += read_available(child, pexpect, timeout=1)

        child.sendline("/status")
        time.sleep(4)
        buf += read_available(child, pexpect)

        child.sendline("/exit")
        try:
            child.expect(pexpect.EOF, timeout=8)
            buf += child.before or ""
        except pexpect.TIMEOUT:
            child.sendcontrol("c")
            child.close(force=True)
    finally:
        if child.isalive():
            child.close(force=True)

    summary = extract_codex_usage_summary(buf)
    if summary:
        return summary

    text = strip_ansi(buf)
    matched = [
        clean_status_line(line)
        for line in text.splitlines()
        if line.strip() and STATUS_LINE_RE.search(line)
    ]
    matched = [line for line in matched if line]
    return "\n".join(unique_lines(matched)) if matched else text.strip()


def main(argv: list[str]) -> int:
    cli = argv[1] if len(argv) > 1 else "codex"
    if cli != "codex":
        print(f"{cli} 暂未支持自动用量检查")
        return 0

    try:
        output = collect_codex_status()
    except Exception as err:
        output = f"Codex 用量检查失败：{err}"
    print(output or "未获取到 Codex 用量信息")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
