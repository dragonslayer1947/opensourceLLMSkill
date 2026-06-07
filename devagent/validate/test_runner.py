"""Post-apply test runner with auto-rollback (V3).

After a run applies its changes, the project's test suite is run once; if it fails, the whole
session is rolled back from its snapshots. Distinct from the per-subtask gate (syntax/types/
lint/security) — this is the full suite as the final safety net."""
from __future__ import annotations

import shlex
import shutil
import subprocess
from pathlib import Path

SKIP_DIRS = {".git", ".devagent", "__pycache__", ".venv", "venv", "node_modules"}


def _has_python_tests(root: Path) -> bool:
    if (root / "tests").is_dir():
        return True
    for pattern in ("test_*.py", "*_test.py"):
        for p in root.rglob(pattern):
            if not any(part in SKIP_DIRS for part in p.parts):
                return True
    return False


def find_test_command(root: Path, gate_cfg: dict) -> str | None:
    """The command to run the suite, or None if no tests are detected / runner missing."""
    cmd = gate_cfg.get("test_command", "pytest -q")
    exe = shlex.split(cmd)[0] if cmd else ""
    if not exe or shutil.which(exe) is None:
        return None
    if exe == "pytest" and not _has_python_tests(root):
        return None
    return cmd


def run_tests(root: Path, command: str, timeout: int = 600) -> tuple[bool, str]:
    try:
        p = subprocess.run(shlex.split(command), cwd=str(root), capture_output=True,
                           text=True, timeout=timeout)
    except FileNotFoundError:
        return True, "test runner not found (skipped)"
    except subprocess.TimeoutExpired:
        return False, "tests timed out"
    return p.returncode == 0, (p.stdout + p.stderr)[-2000:]
