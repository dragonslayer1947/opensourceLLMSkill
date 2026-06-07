"""PreToolUse hook — enforce local-model execution when a repo opts in.

Claude Code runs this before Edit/Write/MultiEdit. When enforcement is ON for the target repo
(a `.devagent/ENFORCE` sentinel exists, or `DEVAGENT_ENFORCE=1`), a direct edit to a *source*
file is **blocked** (exit code 2) with a message telling the host to route the implementation
through `devagent` instead — so the local model writes the code and the host only plans + verifies.

Opt-in and reversible by design:
- does nothing unless the repo has `.devagent/ENFORCE` (set via `devagent enforce on`);
- escape hatch: `DEVAGENT_BYPASS=1`;
- allows `.devagent/` files, `plan.json`, notebooks, docs, and all non-source files;
- **fail-open**: any error allows the edit, so a hook bug can never wedge your editing.

Run as: `python -m devagent.hooks.enforce_local` (Claude Code pipes the tool call as JSON on stdin)."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

BLOCK_TOOLS = {"Edit", "Write", "MultiEdit"}
SOURCE_SUFFIXES = {
    ".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".go", ".java", ".rb", ".rs", ".c", ".h",
    ".cc", ".cpp", ".hpp", ".cs", ".kt", ".swift", ".php", ".scala", ".m", ".mm", ".sql",
}

MESSAGE = (
    "BLOCKED by devagent: local-execution enforcement is ON (default).\n"
    "Do NOT hand-write {path}. Decompose the task and route the implementation through the "
    "local model instead:\n"
    "  1) write the subtasks as JSON (each ≤3 files, scoped for Qwen3.6 27B),\n"
    "  2) devagent plan-import --task \"<task>\" --file plan.json --strict\n"
    "  3) devagent run --from-plan <id>\n"
    "Your role is to plan + verify (read the diff, the gate result, the goal) — not to author. "
    "If a piece fails, split it further.\n"
    "To disable: `devagent enforce off` (global) · `devagent enforce off --repo` (this repo only) "
    "· or DEVAGENT_BYPASS=1 for one session."
)


def global_state_path() -> Path:
    return Path.home() / ".devagent" / "enforce-disabled"


def is_globally_enabled() -> bool:
    """Enforcement is ON by default; a global 'enforce-disabled' flag turns it off everywhere."""
    return not global_state_path().exists()


def set_global_enabled(enabled: bool) -> None:
    p = global_state_path()
    if enabled:
        if p.exists():
            p.unlink()
    else:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("devagent enforcement globally disabled\n", encoding="utf-8")


def enforcement_active(file_path: str, cwd: str) -> bool:
    """True if enforcement applies. Precedence: session bypass → per-repo DISABLE/ENFORCE →
    env DEVAGENT_ENFORCE → global default (ON). Per-repo wins so you can opt a repo out (or in)
    regardless of the global setting."""
    if os.environ.get("DEVAGENT_BYPASS") == "1":
        return False
    base = Path(file_path).resolve().parent if file_path else Path(cwd or ".").resolve()
    for d in [base, *base.parents]:
        dev = d / ".devagent"
        if (dev / "DISABLE").exists():
            return False
        if (dev / "ENFORCE").exists():
            return True
        if (d / ".git").exists():
            break
    env = os.environ.get("DEVAGENT_ENFORCE")
    if env == "0":
        return False
    if env == "1":
        return True
    return is_globally_enabled()


def should_block(tool_name: str, file_path: str, cwd: str) -> bool:
    if tool_name not in BLOCK_TOOLS or not file_path:
        return False
    norm = file_path.replace("\\", "/")
    if "/.devagent/" in norm or norm.rsplit("/", 1)[-1] == "plan.json":
        return False
    if Path(file_path).suffix.lower() not in SOURCE_SUFFIXES:
        return False
    return enforcement_active(file_path, cwd)


def main() -> int:
    try:
        data = json.loads(sys.stdin.read().lstrip("﻿"))  # tolerate a BOM
    except Exception:  # noqa: BLE001 — fail open
        return 0
    ti = data.get("tool_input", {}) or {}
    file_path = ti.get("file_path") or ti.get("path") or ti.get("notebook_path") or ""
    if should_block(data.get("tool_name", ""), file_path, data.get("cwd", "")):
        sys.stderr.write(MESSAGE.format(path=file_path))
        return 2
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001 — never wedge editing on a hook error
        sys.exit(0)
