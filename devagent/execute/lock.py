"""File write-locks — prevent two runs (and, in V3, two parallel agents) from writing the same
files at once. A lock is a small JSON file under `.devagent/locks/`, keyed by a hash of the
target path. Locks held by the same session are reentrant; stale locks (older than a timeout)
are reclaimed automatically so a crashed run never wedges the repo."""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

LOCK_DIR = ".devagent/locks"
STALE_SECONDS = 3600


def lock_dir(root: Path) -> Path:
    return root / LOCK_DIR


def _lockfile(d: Path, rel_path: str) -> Path:
    h = hashlib.sha1(rel_path.replace("\\", "/").encode("utf-8")).hexdigest()[:16]
    return d / f"{h}.lock"


def _read(lf: Path) -> dict:
    try:
        return json.loads(lf.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def acquire(root: Path, paths, session_id: str, stale_seconds: int = STALE_SECONDS):
    """Acquire locks on all paths atomically. Returns (acquired, conflicts) where conflicts is
    a list of (path, holder_info); if non-empty, nothing was acquired."""
    paths = sorted(set(paths))
    if not paths:
        return [], []
    d = lock_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    now = time.time()

    conflicts = []
    for p in paths:
        lf = _lockfile(d, p)
        if lf.exists():
            info = _read(lf)
            if info.get("session_id") == session_id:
                continue  # reentrant
            if now - float(info.get("acquired_at", 0)) > stale_seconds:
                continue  # stale -> reclaimable
            conflicts.append((p, info))
    if conflicts:
        return [], conflicts

    acquired = []
    for p in paths:
        lf = _lockfile(d, p)
        lf.write_text(json.dumps({
            "path": p, "session_id": session_id, "pid": os.getpid(), "acquired_at": now,
        }), encoding="utf-8")
        acquired.append(p)
    return acquired, []


def release(root: Path, paths, session_id: str) -> int:
    d = lock_dir(root)
    released = 0
    for p in set(paths):
        lf = _lockfile(d, p)
        if lf.exists():
            info = _read(lf)
            if not info or info.get("session_id") == session_id:
                lf.unlink()
                released += 1
    return released
