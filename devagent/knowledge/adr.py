"""Architecture Decision Records — machine-readable, enforced semantically.

ADRs live as YAML under `.devagent/adrs/`. Accepted ADRs' decisions are injected into the
executor prompt so generation follows them (proactive), and `devagent adr check` runs a
semantic violation check via the local model on a diff — NOT regex (gap #4), so it catches
violations a keyword rule would miss and avoids false positives a keyword rule would raise."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ADR_DIR = ".devagent/adrs"

SAMPLE_ADR = """\
id: "0001"
title: "Use cursor-based pagination for all list APIs"
status: accepted            # draft | accepted | deprecated | superseded
date: "2026-06-07"
affects_services: [product-service, search-service]
decision: >
  All list endpoints must use cursor-based pagination. Offset-based pagination is
  prohibited on tables larger than 10k rows.
consequences:
  - New list endpoints accept `cursor` and `limit` params.
  - Responses include `next_cursor` and `has_more`.
generates_constraints:
  - id: C-0001-a
    rule: "List endpoints must use cursor pagination, not offset."
    severity: block         # block | warn | log
"""

CHECK_SYSTEM = """\
You check a code diff against the project's architecture decisions (ADRs). For EACH decision the
diff clearly violates, emit one object. Judge semantically — do not flag things that merely look
related. Output ONLY a JSON array (possibly empty), each element:
  {"adr_id": "<id>", "reason": "<one sentence>"}
"""


@dataclass
class Constraint:
    id: str
    rule: str
    severity: str = "warn"


@dataclass
class ADR:
    id: str
    title: str
    status: str = "draft"
    decision: str = ""
    consequences: list[str] = field(default_factory=list)
    affects_services: list[str] = field(default_factory=list)
    constraints: list[Constraint] = field(default_factory=list)
    raw: dict = field(default_factory=dict)

    @property
    def is_active(self) -> bool:
        return self.status == "accepted"


def _parse(data: dict) -> ADR:
    constraints = []
    for c in data.get("generates_constraints", []) or []:
        constraints.append(Constraint(
            id=str(c.get("id", "?")), rule=str(c.get("rule", "")),
            severity=str(c.get("severity", "warn")),
        ))
    return ADR(
        id=str(data.get("id", "?")),
        title=str(data.get("title", "")),
        status=str(data.get("status", "draft")),
        decision=str(data.get("decision", "")).strip(),
        consequences=[str(x) for x in data.get("consequences", []) or []],
        affects_services=[str(x) for x in data.get("affects_services", []) or []],
        constraints=constraints,
        raw=data,
    )


def adr_dir(root: Path) -> Path:
    return root / ADR_DIR


def load_adrs(root: Path) -> list[ADR]:
    d = adr_dir(root)
    if not d.exists():
        return []
    out = []
    for p in sorted(d.glob("*.yaml")) + sorted(d.glob("*.yml")):
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        if isinstance(data, dict) and data.get("id"):
            out.append(_parse(data))
    return out


def active(adrs: list[ADR]) -> list[ADR]:
    return [a for a in adrs if a.is_active]


def write_sample(root: Path) -> Path:
    d = adr_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    p = d / "0001-cursor-pagination.yaml"
    if not p.exists():
        p.write_text(SAMPLE_ADR, encoding="utf-8")
    return p


def constraints_context(adrs: list[ADR]) -> str:
    """Concise text of accepted decisions, injected into the executor prompt."""
    lines = []
    for a in active(adrs):
        lines.append(f"- [{a.id}] {a.title}: {a.decision}")
        for c in a.constraints:
            lines.append(f"    • ({c.severity}) {c.rule}")
    return "\n".join(lines)


def _parse_violations(text: str) -> list[dict]:
    m = re.search(r"\[.*\]", text or "", re.DOTALL)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    return [v for v in data if isinstance(v, dict) and v.get("adr_id")]


def check_violations(adrs: list[ADR], diff: str, router) -> list[dict]:
    """Semantic check of a diff against accepted ADRs via the local model (~$0)."""
    act = active(adrs)
    if not act or not diff.strip():
        return []
    decisions = constraints_context(adrs)
    user = f"PROJECT DECISIONS:\n{decisions}\n\nDIFF:\n{diff[:8000]}\n\nReturn the JSON array."
    result = router.complete("classifier", CHECK_SYSTEM, user, max_tokens=500)
    return _parse_violations(result.text)
