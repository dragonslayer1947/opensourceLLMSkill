"""Cross-team reservation system for shared resources.

Write-locks (`execute.lock`) protect files for the duration of a single run. Reservations are the
*long-horizon, cross-team* analogue: a team announces "I am about to work on `service:payments`
(or `table:orders`, or `file:billing/api.py`) for the next two days," so other teams' epics can
detect the contention up front (`longhorizon.conflict`) instead of colliding mid-flight.

A reservation is a small JSON file under `.devagent/reservations/`, keyed by a hash of the
resource string. It carries an owner (a team/person), the holding session, an acquired timestamp,
and a TTL. Expired reservations are ignored and reclaimable, so a forgotten reservation never
wedges a resource forever. Resource strings are free-form but conventionally `type:name`
(`service:…`, `table:…`, `file:…`)."""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

RES_DIR = ".devagent/reservations"
DEFAULT_TTL_SECONDS = 48 * 3600  # two days — a long-horizon default


@dataclass
class Reservation:
    resource: str
    owner: str
    session_id: str
    acquired_at: float
    ttl_seconds: int = DEFAULT_TTL_SECONDS
    note: str = ""

    def expires_at(self) -> float:
        return self.acquired_at + self.ttl_seconds

    def is_active(self, now: float | None = None) -> bool:
        return (now or time.time()) < self.expires_at()

    def to_dict(self) -> dict:
        return {
            "resource": self.resource, "owner": self.owner, "session_id": self.session_id,
            "acquired_at": self.acquired_at, "ttl_seconds": self.ttl_seconds, "note": self.note,
        }


def reservations_dir(root: Path) -> Path:
    return root / RES_DIR


def _resfile(d: Path, resource: str) -> Path:
    h = hashlib.sha1(resource.strip().encode("utf-8")).hexdigest()[:16]
    return d / f"{h}.json"


def _read(p: Path) -> Reservation | None:
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return Reservation(
        resource=str(data.get("resource", "")), owner=str(data.get("owner", "")),
        session_id=str(data.get("session_id", "")),
        acquired_at=float(data.get("acquired_at", 0)),
        ttl_seconds=int(data.get("ttl_seconds", DEFAULT_TTL_SECONDS)),
        note=str(data.get("note", "")),
    )


def reserve(root: Path, resource: str, owner: str, session_id: str, *,
            ttl_seconds: int = DEFAULT_TTL_SECONDS, note: str = "",
            now: float | None = None):
    """Reserve a resource. Returns (reservation, conflict): on success `reservation` is set and
    `conflict` is None; if an active reservation by *another* owner exists, returns (None, that).
    Re-reserving by the same owner refreshes the timestamp/TTL (idempotent)."""
    now = now if now is not None else time.time()
    d = reservations_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    p = _resfile(d, resource)
    if p.exists():
        existing = _read(p)
        if existing and existing.is_active(now) and existing.owner != owner:
            return None, existing
    res = Reservation(resource.strip(), owner, session_id, now, ttl_seconds, note)
    p.write_text(json.dumps(res.to_dict(), indent=2), encoding="utf-8")
    return res, None


def release(root: Path, resource: str, owner: str) -> bool:
    """Release a reservation. Only the owner may release; returns True if a file was removed."""
    d = reservations_dir(root)
    p = _resfile(d, resource)
    if not p.exists():
        return False
    existing = _read(p)
    if existing and existing.owner != owner:
        return False
    p.unlink()
    return True


def load_reservations(root: Path) -> list[Reservation]:
    d = reservations_dir(root)
    if not d.exists():
        return []
    out = []
    for p in sorted(d.glob("*.json")):
        r = _read(p)
        if r:
            out.append(r)
    return out


def active(root: Path, now: float | None = None) -> list[Reservation]:
    now = now if now is not None else time.time()
    return [r for r in load_reservations(root) if r.is_active(now)]


def prune(root: Path, now: float | None = None) -> int:
    """Delete expired reservations. Returns the count removed."""
    now = now if now is not None else time.time()
    d = reservations_dir(root)
    removed = 0
    for p in (d.glob("*.json") if d.exists() else []):
        r = _read(p)
        if r and not r.is_active(now):
            p.unlink()
            removed += 1
    return removed


def default_session() -> str:
    return f"{os.getpid()}-{int(time.time())}"
