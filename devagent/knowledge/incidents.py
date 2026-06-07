"""Incident knowledge (V4). Past incidents recorded under `.devagent/incidents/*.yaml` carry
the files involved and the lesson learned. When a task touches those files, the lesson is
surfaced and injected into the executor prompt so the system doesn't repeat history."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

INCIDENT_DIR = ".devagent/incidents"

SAMPLE_INCIDENT = """\
id: "INC-001"
title: "Checkout double-charge on retry"
date: "2026-05-20"
files:
  - svc/checkout/charge.py
lesson: >
  Payment retries must be idempotent — key on the client request id, never re-issue a charge
  without checking for an existing one.
"""


@dataclass
class Incident:
    id: str
    title: str = ""
    lesson: str = ""
    date: str = ""
    files: list[str] = field(default_factory=list)


def incident_dir(root: Path) -> Path:
    return root / INCIDENT_DIR


def load_incidents(root: Path) -> list[Incident]:
    d = incident_dir(root)
    if not d.exists():
        return []
    out = []
    for p in sorted(d.glob("*.yaml")) + sorted(d.glob("*.yml")):
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        if isinstance(data, dict) and data.get("id"):
            out.append(Incident(
                id=str(data["id"]), title=str(data.get("title", "")),
                lesson=str(data.get("lesson", "")).strip(), date=str(data.get("date", "")),
                files=[str(f) for f in data.get("files", []) or []],
            ))
    return out


def write_sample(root: Path) -> Path:
    d = incident_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    p = d / "INC-001.yaml"
    if not p.exists():
        p.write_text(SAMPLE_INCIDENT, encoding="utf-8")
    return p


def for_files(incidents: list[Incident], files) -> list[Incident]:
    """Incidents whose recorded files intersect the given paths (by full path or basename)."""
    targets = {f.replace("\\", "/") for f in files}
    bases = {t.rsplit("/", 1)[-1] for t in targets}
    out = []
    for inc in incidents:
        inc_files = {f.replace("\\", "/") for f in inc.files}
        inc_bases = {f.rsplit("/", 1)[-1] for f in inc_files}
        if (inc_files & targets) or (inc_bases & bases):
            out.append(inc)
    return out


def lessons_context(incidents: list[Incident]) -> str:
    return "\n".join(f"- [{i.id}] {i.title}: {i.lesson}" for i in incidents)
