"""Multi-day task-graph runner with checkpointing and resume.

An epic may take days and span many sessions. The runner separates the *plan* (immutable, in
`epic.yaml`) from *progress* (mutable, in `state.json`), and checkpoints to disk after every
single status change. Crash, close the laptop, come back tomorrow — re-running the epic simply
skips everything already `done` and continues from the frontier of ready tasks.

A task is **ready** when every dependency is `done` (deps that don't exist are ignored, mirroring
the wave scheduler) and the task itself is still `pending`. Story/epic status rolls up from the
leaves: a story is `done` when all its tasks are done, `failed`/`blocked` if any leaf is.

Execution itself is injected (`execute_fn`) so the orchestration is testable offline; the CLI
wires it to `pipeline.run`, while tests pass a fake."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .epic import Epic, Node

PENDING, IN_PROGRESS, DONE, FAILED, BLOCKED = (
    "pending", "in_progress", "done", "failed", "blocked")
TERMINAL = {DONE, FAILED}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def state_path(root: Path, epic_id: str) -> Path:
    return root / ".devagent" / "epics" / epic_id / "state.json"


def init_state(epic: Epic) -> dict:
    return {
        "epic_id": epic.id,
        "status": {n.id: PENDING for n in epic.nodes},
        "updated": _now(),
        "log": [],
    }


def load_state(root: Path, epic: Epic) -> dict:
    p = state_path(root, epic.id)
    if not p.exists():
        return init_state(epic)
    data = json.loads(p.read_text(encoding="utf-8"))
    # Reconcile with the plan: new nodes start pending; dropped nodes fall away.
    status = {n.id: data.get("status", {}).get(n.id, PENDING) for n in epic.nodes}
    data["status"] = status
    return data


def save_state(root: Path, state: dict) -> None:
    p = state_path(root, state["epic_id"])
    p.parent.mkdir(parents=True, exist_ok=True)
    state["updated"] = _now()
    p.write_text(json.dumps(state, indent=2), encoding="utf-8")


def status_of(state: dict, node_id: str) -> str:
    return state.get("status", {}).get(node_id, PENDING)


def set_status(state: dict, node_id: str, status: str, note: str = "") -> None:
    state.setdefault("status", {})[node_id] = status
    state.setdefault("log", []).append(
        {"ts": _now(), "node": node_id, "status": status, "note": note})


def task_ready(epic: Epic, state: dict, task: Node) -> bool:
    if status_of(state, task.id) != PENDING:
        return False
    known = {n.id for n in epic.nodes}
    return all(status_of(state, d) == DONE for d in task.depends_on if d in known)


def ready_tasks(epic: Epic, state: dict) -> list[Node]:
    return [t for t in epic.tasks() if task_ready(epic, state, t)]


def rollup(epic: Epic, state: dict) -> None:
    """Propagate leaf status up to stories and the epic (done/failed/blocked/in_progress)."""
    for parent in epic.stories() + ([epic.root] if epic.root else []):
        kids = epic.children_of(parent.id)
        if not kids:
            continue
        kid_status = [status_of(state, k.id) for k in kids]
        if all(s == DONE for s in kid_status):
            new = DONE
        elif any(s == FAILED for s in kid_status):
            new = FAILED
        elif any(s in (IN_PROGRESS, DONE) for s in kid_status):
            new = IN_PROGRESS
        else:
            new = PENDING
        if status_of(state, parent.id) != new:
            set_status(state, parent.id, new, note="rollup")


def progress(epic: Epic, state: dict) -> dict:
    tasks = epic.tasks()
    done = sum(1 for t in tasks if status_of(state, t.id) == DONE)
    failed = sum(1 for t in tasks if status_of(state, t.id) == FAILED)
    return {
        "tasks": len(tasks), "done": done, "failed": failed,
        "remaining": len(tasks) - done - failed,
        "pct": round(100 * done / len(tasks)) if tasks else 100,
    }


def run_epic(root: Path, epic: Epic, execute_fn, *, max_tasks: int | None = None,
             on_event=None) -> dict:
    """Drive the epic to completion (or until `max_tasks` leaves run this session).

    `execute_fn(task) -> (ok: bool, note: str)` runs one leaf. State is checkpointed before and
    after each task, so an interrupted run resumes cleanly. Returns the progress summary."""
    state = load_state(root, epic)
    rollup(epic, state)
    save_state(root, state)
    ran = 0

    def emit(kind: str, **detail):
        if on_event:
            on_event(kind, detail)

    while True:
        if max_tasks is not None and ran >= max_tasks:
            emit("budget", ran=ran)
            break
        ready = ready_tasks(epic, state)
        if not ready:
            break
        task = ready[0]
        emit("start", task=task)
        set_status(state, task.id, IN_PROGRESS)
        save_state(root, state)                      # checkpoint: claimed
        try:
            ok, note = execute_fn(task)
        except Exception as e:  # noqa: BLE001 — a crashing task must not wedge the graph
            ok, note = False, f"exception: {e}"
        set_status(state, task.id, DONE if ok else FAILED, note=note)
        rollup(epic, state)
        save_state(root, state)                      # checkpoint: result
        ran += 1
        emit("finish", task=task, ok=ok, note=note)

    return {**progress(epic, state), "ran": ran, "state": state}
