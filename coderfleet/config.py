from __future__ import annotations

import os
import re
from pathlib import Path


DEFAULT_WORKSPACE = Path.home() / ".coderfleet"


def get_workspace() -> Path:
    """
    Resolve CoderFleet workspace directory.
    Priority: CODERFLEET_WORKSPACE env var → ~/.coderfleet/
    """
    env = os.environ.get("CODERFLEET_WORKSPACE", "")
    if env:
        return Path(env).expanduser().resolve()
    return DEFAULT_WORKSPACE


def ensure_workspace(ws: Path) -> None:
    """Create workspace and required subdirectories."""
    for sub in ("accounts", "tasks", "conversations"):
        (ws / sub).mkdir(parents=True, exist_ok=True)


def parse_conf(path: Path) -> list[dict[str, str]]:
    """
    Parse a KEY=value space-tokenized .conf file.
    Each non-comment, non-blank line becomes a dict.
    """
    records: list[dict[str, str]] = []
    if not path.exists():
        return records
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip().rstrip("\r")
        if not line or line.startswith("#"):
            continue
        record: dict[str, str] = {}
        for token in line.split():
            if "=" in token:
                k, v = token.split("=", 1)
                record[k.upper()] = v
        if record:
            records.append(record)
    return records


def write_conf_line(path: Path, tokens: dict[str, str]) -> None:
    """Append a KEY=value line to a conf file."""
    parts = [f"{k}={v}" for k, v in tokens.items()]
    with path.open("a", encoding="utf-8") as f:
        f.write("  ".join(parts) + "\n")


def remove_conf_entry(path: Path, key: str, value: str) -> None:
    """Remove lines where KEY=value appears as a token, rewrite file in-place."""
    pattern = re.compile(rf"\b{re.escape(key)}={re.escape(value)}(\s|$)")
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    path.write_text("".join(l for l in lines if not pattern.search(l)), encoding="utf-8")


def load_config(ws: Path) -> dict[str, str]:
    """Load config.conf from workspace into a flat dict."""
    result: dict[str, str] = {}
    for record in parse_conf(ws / "config.conf"):
        result.update(record)
    return result
