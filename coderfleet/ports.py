from __future__ import annotations

import random
import socket
from collections.abc import Iterable


IDE_PORT_MIN = 18080
IDE_PORT_MAX = 29999


def is_port_available(port: int, host: str = "127.0.0.1") -> bool:
    """Return True when host:port can be bound by a local service."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def allocate_ide_port(
    used_ports: Iterable[int | None] = (),
    host: str = "127.0.0.1",
    min_port: int = IDE_PORT_MIN,
    max_port: int = IDE_PORT_MAX,
    attempts: int = 200,
) -> int:
    used = {port for port in used_ports if port is not None}
    candidates = [port for port in range(min_port, max_port + 1) if port not in used]
    if not candidates:
        raise RuntimeError(f"没有可用的 IDE 端口候选范围：{min_port}-{max_port}")

    for _ in range(min(attempts, len(candidates))):
        port = random.choice(candidates)
        if is_port_available(port, host):
            return port
        candidates.remove(port)

    for port in candidates:
        if is_port_available(port, host):
            return port

    raise RuntimeError(f"没有找到空闲 IDE 端口：{host}:{min_port}-{max_port}")
