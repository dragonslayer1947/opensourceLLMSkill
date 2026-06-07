"""Per-run decision trail.

A `Trace` accumulates ordered events as the pipeline runs and is persisted to
`.devagent/traces/<session>.json`. It is deliberately cheap and best-effort: recording never
raises into the pipeline, and an absent trace simply means a run predates this feature.

Events are free-form `(kind, detail)` pairs; the well-known kinds the pipeline emits are
`index`, `retrieve`, `routing`, `rules`, `incidents`, `contract`, `decompose`, `blast_radius`,
`subtask`, and `final`. `devagent trace` renders them into a readable timeline plus a roll-up of
per-subtask cost, time, model, and the blast radius the run carried."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

TRACES_DIR = ".devagent/traces"


@dataclass
class Event:
    seq: int
    kind: str
    elapsed_s: float
    detail: dict

    def to_dict(self) -> dict:
        return {"seq": self.seq, "kind": self.kind, "elapsed_s": round(self.elapsed_s, 3),
                "detail": self.detail}


@dataclass
class Trace:
    session_id: str
    task: str = ""
    started: str = ""
    events: list[Event] = field(default_factory=list)
    _t0: float = field(default_factory=time.monotonic, repr=False)

    def record(self, kind: str, **detail) -> None:
        try:
            self.events.append(Event(len(self.events) + 1, kind,
                                     time.monotonic() - self._t0, detail))
        except Exception:  # noqa: BLE001 — tracing must never break a run
            pass

    def to_dict(self) -> dict:
        return {"session_id": self.session_id, "task": self.task, "started": self.started,
                "events": [e.to_dict() for e in self.events]}

    def save(self, root: Path) -> Path | None:
        try:
            d = root / TRACES_DIR
            d.mkdir(parents=True, exist_ok=True)
            p = d / f"{self.session_id}.json"
            p.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
            return p
        except Exception:  # noqa: BLE001
            return None


def new_trace(session_id: str, task: str = "") -> Trace:
    return Trace(session_id=session_id, task=task,
                 started=datetime.now(timezone.utc).isoformat(timespec="seconds"))


def traces_dir(root: Path) -> Path:
    return root / TRACES_DIR


def load_trace(root: Path, session_id: str) -> dict | None:
    p = traces_dir(root) / f"{session_id}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def list_traces(root: Path) -> list[str]:
    d = traces_dir(root)
    if not d.exists():
        return []
    return sorted(p.stem for p in d.glob("*.json"))


def latest(root: Path) -> str | None:
    ids = list_traces(root)
    return ids[-1] if ids else None


def summarize(trace: dict) -> dict:
    """Roll up a loaded trace: per-subtask cost/time/model + the blast radius the run carried."""
    events = trace.get("events", [])
    subtasks = [e["detail"] for e in events if e.get("kind") == "subtask"]
    blast = next((e["detail"] for e in events if e.get("kind") == "blast_radius"), {})
    total_cost = round(sum(float(s.get("cost_usd", 0) or 0) for s in subtasks), 6)
    return {
        "subtasks": subtasks,
        "blast_radius": blast,
        "total_cost": total_cost,
        "n_events": len(events),
    }
