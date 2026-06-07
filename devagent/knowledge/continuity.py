"""Auto-updated continuity memory (gap #6).

ADRs, patterns, and incidents are hand-curated. Nothing records what *actually happened* run to
run — so each run starts cold about the changes the previous ones made. On a long-lived 100k-LOC
codebase that's how drift creeps in: a later change contradicts an interface an earlier run
established and no one remembers.

This keeps a rolling, automatic ledger of completed changes — task, files touched, and the
interfaces declared — written after every successful run and injected (the entries relevant to the
files a new task touches) into the next run's context. It's the codebase's short-term memory."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import yaml

CONTINUITY_FILE = ".devagent/continuity.yaml"
MAX_ENTRIES = 40


def _path(root: Path) -> Path:
    return root / CONTINUITY_FILE


def load(root: Path) -> list[dict]:
    p = _path(root)
    if not p.exists():
        return []
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or []
    except (OSError, yaml.YAMLError):
        return []
    return data if isinstance(data, list) else []


def record(root: Path, *, task: str, files: list[str], provides: list[str],
           session_id: str) -> None:
    """Append a completed-change entry (most-recent last), capped at MAX_ENTRIES."""
    entries = load(root)
    entries.append({
        "when": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "session": session_id,
        "task": task.strip()[:300],
        "files": sorted(set(files)),
        "provides": sorted(set(provides)),
    })
    entries = entries[-MAX_ENTRIES:]
    p = _path(root)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(yaml.safe_dump(entries, sort_keys=False, allow_unicode=True), encoding="utf-8")
    except OSError:
        pass  # continuity memory is best-effort, never fails a run


def recent_context(root: Path, candidate_files: list[str] | None = None, limit: int = 6) -> str:
    """A compact context block of prior changes — those touching the same files first, then the
    most recent. Injected into a new run so it builds ON the established interfaces, not against."""
    entries = load(root)
    if not entries:
        return ""
    cand = {c.replace("\\", "/") for c in (candidate_files or [])}
    relevant = [e for e in entries if cand & set(e.get("files", []))]
    chosen = (relevant or entries)[-limit:]
    lines = []
    for e in reversed(chosen):
        files = ", ".join(e.get("files", [])[:5])
        prov = "; ".join(e.get("provides", [])[:4])
        line = f"- {e.get('task', '')[:120]} → touched: {files}"
        if prov:
            line += f"  [interfaces: {prov}]"
        lines.append(line)
    return "\n".join(lines)
