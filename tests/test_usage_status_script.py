from __future__ import annotations

import importlib.util
import py_compile
from pathlib import Path
from types import ModuleType


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "coderfleet_usage_status.py"
DOCKERFILE = ROOT / "Dockerfile"


def load_usage_status_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("coderfleet_usage_status", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_usage_status_script_is_valid_python() -> None:
    py_compile.compile(str(SCRIPT), doraise=True)


def test_dockerfile_installs_usage_status_helper() -> None:
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")

    assert "pexpect" in dockerfile
    assert "coderfleet_usage_status.py" in dockerfile
    assert "/usr/local/bin/coderfleet-usage-status" in dockerfile


def test_extract_codex_usage_summary_from_status_box() -> None:
    module = load_usage_status_module()
    status = """
/status

╭─────────────────────────────────────────────────────────────────────────────────╮
│  >_ OpenAI Codex (v0.130.0)                                                     │
│                                                                                 │
│  Model:                gpt-5.5 (reasoning medium, summaries auto)               │
│  Account:              zhchxiao123@proton.me (Plus)                             │
│                                                                                 │
│  5h limit:             [███████████████████░] 95% left (resets 03:53)           │
│  Weekly limit:         [███████████████████░] 96% left (resets 10:08 on 25 May) │
╰─────────────────────────────────────────────────────────────────────────────────╯
"""

    assert module.extract_codex_usage_summary(status) == (
        "5h limit: 95% left (resets 03:53)\n"
        "Weekly limit: 96% left (resets 10:08 on 25 May)"
    )
