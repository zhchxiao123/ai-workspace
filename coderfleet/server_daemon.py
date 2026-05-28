from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path


def pid_file(ws: Path) -> Path:
    return ws / "server.pid"


def log_file(ws: Path) -> Path:
    return ws / "server.log"


def is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def read_pid(ws: Path) -> int | None:
    p = pid_file(ws)
    if not p.exists():
        return None
    try:
        return int(p.read_text().strip())
    except ValueError:
        return None


def server_is_running(ws: Path) -> bool:
    pid = read_pid(ws)
    return bool(pid and is_running(pid))


def start_daemon(ws: Path, port: int) -> int:
    """Start server in daemon mode. Returns PID. No-ops if already running."""
    pid = read_pid(ws)
    if pid and is_running(pid):
        return pid

    log = log_file(ws)
    log.parent.mkdir(parents=True, exist_ok=True)

    with log.open("a") as fh:
        proc = subprocess.Popen(
            [sys.executable, "-m", "coderfleet", "server", "--port", str(port)],
            stdout=fh,
            stderr=fh,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            env={**os.environ, "CODERFLEET_WORKSPACE": str(ws), "CODERFLEET_PORT": str(port)},
        )

    pid_file(ws).write_text(str(proc.pid))
    time.sleep(1)

    if is_running(proc.pid):
        return proc.pid

    pid_file(ws).unlink(missing_ok=True)
    raise RuntimeError(f"Server failed to start — check log: {log}")


def stop_daemon(ws: Path) -> bool:
    """Stop server daemon. Returns True if it was running, False if already stopped."""
    pid = read_pid(ws)
    if pid is None or not is_running(pid):
        pid_file(ws).unlink(missing_ok=True)
        return False

    os.kill(pid, signal.SIGTERM)
    for _ in range(20):
        time.sleep(0.5)
        if not is_running(pid):
            break
    else:
        os.kill(pid, signal.SIGKILL)

    pid_file(ws).unlink(missing_ok=True)
    return True
