"""
conftest.py — patch docker at import time so tests run without docker installed.
"""
from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

# Patch subprocess.run before the first docker_mgr import so tests do not require
# Docker to be installed in the environment.
_mock_run = MagicMock(return_value=MagicMock(returncode=0, stdout="Docker Compose version v2.0.0", stderr=""))
with patch("subprocess.run", _mock_run):
    from coderfleet.server import docker_mgr  # noqa: F401
