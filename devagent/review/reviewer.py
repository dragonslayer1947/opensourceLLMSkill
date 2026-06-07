"""Reviewer agent — reads a diff and flags correctness / security / quality issues before the
change is kept. A HIGH-severity finding blocks the change (rolled back), like a gate failure.

The model judges; severity classification + the blocking decision are deterministic here."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

REVIEWER_SYSTEM = """\
You are a strict senior code reviewer. Review the diff for: correctness bugs, security issues,
and clear quality problems. Report only real, actionable issues — not style nitpicks.

Output ONLY a JSON array (possibly empty), each element:
  {"severity": "high" | "medium" | "low", "category": "...", "message": "<one sentence>"}
"high" = a bug, security hole, or data-loss risk that must be fixed before merge.
"""

_SEVERITIES = {"high", "medium", "low"}


@dataclass
class Finding:
    severity: str
    category: str
    message: str


def parse_findings(text: str) -> list[Finding]:
    m = re.search(r"\[.*\]", text or "", re.DOTALL)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    out = []
    for d in data:
        if not isinstance(d, dict) or not d.get("message"):
            continue
        sev = str(d.get("severity", "low")).lower()
        out.append(Finding(sev if sev in _SEVERITIES else "low",
                           str(d.get("category", "general")), str(d["message"])))
    return out


def has_blocking(findings: list[Finding]) -> bool:
    return any(f.severity == "high" for f in findings)


def review_diff(task: str, diff: str, router, role: str = "reviewer") -> tuple[list[Finding], dict]:
    """Returns (findings, call_meta). call_meta carries model/tier/tokens/cost for the ledger."""
    if not diff.strip():
        return [], {}
    user = f"TASK:\n{task}\n\nDIFF:\n{diff[:8000]}\n\nReturn the JSON array of findings."
    result = router.complete(role, REVIEWER_SYSTEM, user, max_tokens=600, cacheable_system=True)
    meta = {"model": result.model_name, "tier": result.tier,
            "tokens_in": result.tokens_in, "tokens_out": result.tokens_out,
            "cost_usd": result.cost_usd}
    return parse_findings(result.text), meta


CHANGESET_SYSTEM = """\
You review a COMPLETE change-set (all files from all subtasks) against the ORIGINAL GOAL. Per-file
review can't see this: your job is whether the pieces, TAKEN TOGETHER, actually achieve the goal
and fit each other. Look for: parts of the goal NOT implemented (missing wiring/registration/
endpoints), inconsistencies between files (one calls an interface another didn't build that way),
and behavior the goal forbids. Report only real, goal-level issues — not per-line style.

Output ONLY a JSON array (possibly empty), each element:
  {"severity": "high" | "medium" | "low", "category": "...", "message": "<one sentence>"}
"high" = the goal is NOT achieved, or the pieces don't fit and the feature will not work."""


def review_changeset(task: str, diff: str, subtask_summaries: list[str], router,
                     role: str = "reviewer") -> tuple[list[Finding], dict]:
    """Whole-changeset-vs-intent review (gap #8): does the COMBINED diff achieve the goal and cohere
    across files? Distinct from review_diff, which judges one subtask's diff in isolation."""
    if not diff.strip():
        return [], {}
    plan = "\n".join(f"- {s}" for s in subtask_summaries) or "(single change)"
    user = (f"GOAL:\n{task}\n\nWHAT WAS BUILT (subtasks):\n{plan}\n\n"
            f"COMBINED DIFF:\n{diff[:12000]}\n\nReturn the JSON array of goal-level findings.")
    result = router.complete(role, CHANGESET_SYSTEM, user, max_tokens=700, cacheable_system=True)
    meta = {"model": result.model_name, "tier": result.tier,
            "tokens_in": result.tokens_in, "tokens_out": result.tokens_out,
            "cost_usd": result.cost_usd}
    return parse_findings(result.text), meta
