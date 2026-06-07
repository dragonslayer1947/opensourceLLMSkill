"""Predictive conflict detection — run *before* execution starts.

The wave scheduler (V3) keeps a single wave file-disjoint, but it only sees the tasks it is
about to run and only at file granularity. Across a whole epic that may run over days, two tasks
can still collide in ways the scheduler won't surface up front:

- **direct**: two tasks target the same file (they cannot run concurrently, and even serially the
  second rewrites the first's assumptions),
- **coupling**: task A edits a file that lives in task B's blast radius (B imports A, transitively)
  — changing both risks a semantic clash the file-disjoint rule never sees,
- **reservation**: a task targets a file under an active cross-team reservation held by someone
  else (see `longhorizon.reservation`).

This is advisory: it returns ranked conflicts so a human (or the runner) can sequence or split
work before committing to it."""
from __future__ import annotations

from dataclasses import dataclass

from ..context.index import RepoIndex
from ..planning import blast_radius


@dataclass
class Conflict:
    kind: str               # direct | coupling | reservation
    a: str                  # task id
    b: str                  # task id (or holder, for reservation)
    detail: str
    severity: str = "warn"  # block | warn

    def render(self) -> str:
        tag = "BLOCK" if self.severity == "block" else "warn"
        return f"[{tag}] {self.kind}: {self.a} ✗ {self.b} — {self.detail}"


def _files(task) -> set[str]:
    return {f.replace("\\", "/") for f in (getattr(task, "target_files", None) or [])}


def detect(tasks: list, index: RepoIndex | None = None,
           reservations: list | None = None, self_session: str | None = None) -> list[Conflict]:
    """Find conflicts among `tasks` (each needs `.id` and `.target_files`).

    `index` enables coupling detection via the import graph; omit it to check files only.
    `reservations` is a list of active reservation records (dicts with `resource`/`owner`/
    `session_id`) — a task touching a reserved file held by another session is flagged."""
    conflicts: list[Conflict] = []
    items = [(t.id, _files(t)) for t in tasks]

    # direct: shared target file between two distinct tasks
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            shared = items[i][1] & items[j][1]
            if shared:
                conflicts.append(Conflict(
                    "direct", items[i][0], items[j][0],
                    f"both write {', '.join(sorted(shared))}", severity="block"))

    # coupling: A's files are in B's blast radius (or vice versa)
    if index is not None:
        dependents = blast_radius.build_dependents(index)
        reach: dict[str, set[str]] = {}
        for tid, files in items:
            r: set[str] = set()
            for f in files:
                r |= dependents.get(f, set())
            reach[tid] = r - files  # exclude self
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                a_id, a_files = items[i]
                b_id, b_files = items[j]
                if a_files & reach[b_id] or b_files & reach[a_id]:
                    overlap = (a_files & reach[b_id]) | (b_files & reach[a_id])
                    conflicts.append(Conflict(
                        "coupling", a_id, b_id,
                        f"import coupling via {', '.join(sorted(overlap))}", severity="warn"))

    # reservation: a task targets a file reserved by another session/owner
    for res in reservations or []:
        resource = str(res.get("resource", ""))
        if res.get("session_id") and res.get("session_id") == self_session:
            continue
        rel = resource.split("file:", 1)[1] if resource.startswith("file:") else resource
        rel = rel.replace("\\", "/")
        for tid, files in items:
            if rel in files:
                conflicts.append(Conflict(
                    "reservation", tid, str(res.get("owner", "?")),
                    f"{rel} reserved by {res.get('owner', '?')}", severity="block"))

    return conflicts


def has_blocking(conflicts: list[Conflict]) -> bool:
    return any(c.severity == "block" for c in conflicts)
