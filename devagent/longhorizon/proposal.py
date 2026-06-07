"""Autonomous architectural proposals — behind a human approval gate.

For a long-horizon goal, a frontier model can propose the architectural decision *before* any
code is written: the context, the decision, the alternatives weighed, and the consequences. But
the system never adopts an architecture on its own — every proposal lands as `status: proposed`
and waits at an explicit approval gate. A human runs `devagent propose --approve <id>` (or
`--reject`); only on approval is the proposal promoted into an enforced ADR
(`devagent.knowledge.adr`), at which point it constrains all future generation.

Proposals live as YAML under `.devagent/proposals/`."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml

PROPOSALS_DIR = ".devagent/proposals"
STATUSES = ("proposed", "approved", "rejected")

PROPOSE_SYSTEM = """\
You are a principal engineer proposing ONE architectural decision for a goal. You do not write
code. Weigh the realistic alternatives and recommend one, with consequences a team will live with.

Return ONLY this JSON:
{
  "title": "<decision, imperative — e.g. 'Use an outbox table for order events'>",
  "context": "<2-4 sentences: the forces at play>",
  "decision": "<what we will do>",
  "alternatives": [{"option": "...", "why_not": "..."}],
  "consequences": ["..."],
  "constraints": [{"rule": "<enforceable rule this implies>", "severity": "block|warn"}]
}
"""


@dataclass
class Proposal:
    id: str
    goal: str
    title: str
    context: str = ""
    decision: str = ""
    alternatives: list[dict] = field(default_factory=list)
    consequences: list[str] = field(default_factory=list)
    constraints: list[dict] = field(default_factory=list)
    status: str = "proposed"
    created: str = ""
    reviewer: str = ""
    planner_model: str | None = None

    def to_yaml(self) -> str:
        return yaml.safe_dump({
            "id": self.id, "goal": self.goal, "title": self.title, "status": self.status,
            "created": self.created, "reviewer": self.reviewer,
            "planner_model": self.planner_model, "context": self.context,
            "decision": self.decision, "alternatives": self.alternatives,
            "consequences": self.consequences, "constraints": self.constraints,
        }, sort_keys=False, allow_unicode=True)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def proposals_dir(root: Path) -> Path:
    return root / PROPOSALS_DIR


def proposal_path(root: Path, pid: str) -> Path:
    return proposals_dir(root) / f"{pid}.yaml"


def next_id(root: Path) -> str:
    d = proposals_dir(root)
    existing = [p.stem for p in d.glob("P-*.yaml")] if d.exists() else []
    nums = [int(m.group(1)) for s in existing if (m := re.match(r"P-(\d+)$", s))]
    return f"P-{(max(nums) + 1) if nums else 1:04d}"


def save_proposal(root: Path, proposal: Proposal) -> Path:
    d = proposals_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    p = proposal_path(root, proposal.id)
    p.write_text(proposal.to_yaml(), encoding="utf-8")
    return p


def _from_dict(data: dict) -> Proposal:
    return Proposal(
        id=str(data.get("id", "?")), goal=str(data.get("goal", "")),
        title=str(data.get("title", "")), context=str(data.get("context", "")),
        decision=str(data.get("decision", "")),
        alternatives=list(data.get("alternatives", []) or []),
        consequences=[str(x) for x in data.get("consequences", []) or []],
        constraints=list(data.get("constraints", []) or []),
        status=str(data.get("status", "proposed")), created=str(data.get("created", "")),
        reviewer=str(data.get("reviewer", "")), planner_model=data.get("planner_model"),
    )


def load_proposal(root: Path, pid: str) -> Proposal | None:
    p = proposal_path(root, pid)
    if not p.exists():
        return None
    return _from_dict(yaml.safe_load(p.read_text(encoding="utf-8")) or {})


def load_proposals(root: Path) -> list[Proposal]:
    d = proposals_dir(root)
    if not d.exists():
        return []
    return [_from_dict(yaml.safe_load(p.read_text(encoding="utf-8")) or {})
            for p in sorted(d.glob("P-*.yaml"))]


def _extract_json_object(text: str) -> dict | None:
    m = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


def propose(root: Path, goal: str, router, *, skeleton: str = "") -> Proposal:
    """Generate an architectural proposal via the planner role and persist it as `proposed`."""
    user = (f"GOAL:\n{goal}\n\nREPO MAP (signatures only):\n{skeleton or '(empty repo)'}\n\n"
            f"Propose one architectural decision.")
    result = router.complete("planner", PROPOSE_SYSTEM, user, max_tokens=1500,
                             cacheable_system=True)
    parsed = _extract_json_object(result.text) or {}
    pid = next_id(root)
    proposal = Proposal(
        id=pid, goal=goal, title=str(parsed.get("title", goal))[:160],
        context=str(parsed.get("context", "")), decision=str(parsed.get("decision", "")),
        alternatives=[a for a in parsed.get("alternatives", []) or [] if isinstance(a, dict)],
        consequences=[str(x) for x in parsed.get("consequences", []) or []],
        constraints=[c for c in parsed.get("constraints", []) or [] if isinstance(c, dict)],
        status="proposed", created=_now(), planner_model=result.model_name,
    )
    save_proposal(root, proposal)
    return proposal


def set_decision(root: Path, pid: str, decision: str, reviewer: str = "") -> Proposal | None:
    """The human approval gate: approve or reject a proposal. Returns the updated proposal."""
    if decision not in ("approved", "rejected"):
        raise ValueError("decision must be 'approved' or 'rejected'")
    proposal = load_proposal(root, pid)
    if not proposal:
        return None
    proposal.status = decision
    proposal.reviewer = reviewer
    save_proposal(root, proposal)
    return proposal


def promote_to_adr(root: Path, proposal: Proposal) -> Path:
    """Write an approved proposal into `.devagent/adrs/` as an accepted ADR so it begins to
    constrain generation. Mirrors the ADR YAML schema in `devagent.knowledge.adr`."""
    from ..knowledge import adr as adr_mod
    d = adr_mod.adr_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    existing = [p.stem for p in d.glob("*.yaml")] + [p.stem for p in d.glob("*.yml")]
    nums = [int(m.group(0)) for s in existing if (m := re.match(r"\d+", s))]
    adr_num = f"{(max(nums) + 1) if nums else 1:04d}"
    constraints = []
    for i, c in enumerate(proposal.constraints, ord("a")):
        constraints.append({
            "id": f"C-{adr_num}-{chr(i)}",
            "rule": str(c.get("rule", "")),
            "severity": str(c.get("severity", "warn")),
        })
    payload = {
        "id": adr_num,
        "title": proposal.title,
        "status": "accepted",
        "date": _now()[:10],
        "decision": proposal.decision or proposal.title,
        "consequences": proposal.consequences,
        "generates_constraints": constraints,
        "source_proposal": proposal.id,
    }
    slug = re.sub(r"[^a-z0-9]+", "-", proposal.title.lower()).strip("-")[:40] or "decision"
    p = d / f"{adr_num}-{slug}.yaml"
    p.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return p
