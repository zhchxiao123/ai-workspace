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


def update_conf_field(path: Path, name: str, key: str, value: str) -> bool:
    """
    In the line where NAME=<name> appears, set KEY=value (replace or append).
    Returns True if the entry was found and updated, False if not found.
    """
    key = key.upper()
    name_pat = re.compile(rf"\bNAME={re.escape(name)}(\s|$)")
    key_pat = re.compile(rf"\b{re.escape(key)}=\S*")

    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    updated = False
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#") or not stripped:
            new_lines.append(line)
            continue
        if name_pat.search(line):
            if key_pat.search(line):
                new_line = key_pat.sub(f"{key}={value}", line)
            else:
                new_line = line.rstrip("\n").rstrip("\r") + f"  {key}={value}\n"
            new_lines.append(new_line)
            updated = True
        else:
            new_lines.append(line)
    if updated:
        path.write_text("".join(new_lines), encoding="utf-8")
    return updated


def set_config(ws: Path, key: str, value: str) -> None:
    """Set or update a single KEY=value entry in config.conf."""
    key = key.upper()
    conf = ws / "config.conf"
    if conf.exists():
        lines = conf.read_text(encoding="utf-8").splitlines(keepends=True)
        pattern = re.compile(rf"(^|\s){re.escape(key)}=\S*")
        updated = False
        new_lines = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("#") or not stripped:
                new_lines.append(line)
                continue
            if pattern.search(line):
                # Replace just the token in-place, preserving surrounding tokens
                new_line = pattern.sub(lambda m: m.group(1) + f"{key}={value}", line)
                new_lines.append(new_line)
                updated = True
            else:
                new_lines.append(line)
        if updated:
            conf.write_text("".join(new_lines), encoding="utf-8")
            return
    # Key not found — append it
    ensure_workspace(ws)
    with conf.open("a", encoding="utf-8") as f:
        f.write(f"{key}={value}\n")
