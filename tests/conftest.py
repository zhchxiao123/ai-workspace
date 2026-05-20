"""
conftest.py — patch docker at import time so tests run without docker installed.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "server"
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))

# docker_mgr calls _dc_cmd() at module level (DC = _dc_cmd()).
# Patch subprocess.run before the first import so it doesn't fail
# when docker is not installed in the test environment.
_mock_run = MagicMock(return_value=MagicMock(returncode=0, stdout="Docker Compose version v2.0.0", stderr=""))
with patch("subprocess.run", _mock_run):
    import docker_mgr  # noqa: F401 — side-effect: DC is set to "docker compose"
