"""Goal-backward plan verification (Tier-1).

The whole thesis trusts the decomposition: Claude splits the goal, the local model builds each
piece. But the plan's *completeness* is never checked — a missing subtask is silent
under-delivery you only discover when the feature half-works. `validate_plan` checks structure
(cycles, dangling deps, envelope); this adds the goal-backward half:

  - `structural_gaps` — deterministic completeness/coherence checks (free): interfaces that
    dependents rely on but that are never declared, and the same file edited by unordered subtasks.
  - `completeness_review` — an optional, cheap model pass that reads the goal + subtasks and names
    REQUIRED steps that are missing (wiring, tests, migrations, error handling). Best-effort: no
    planner or a failure → returns no gaps, never blocks."""
from __future__ import annotations

from ..decompose.planner import Subtask, _extract_json_array

PLAN_CHECK_SYSTEM = """\
You verify that a DECOMPOSITION fully achieves a coding GOAL. Given the goal and the ordered
subtasks, list any REQUIRED steps that are MISSING to actually achieve the goal — e.g. wiring a new
component into the app, registering a route, error handling, tests, data migration, or config.
Be specific and CONSERVATIVE: list only genuinely missing, necessary steps, not nice-to-haves.
Output ONLY a JSON array of short strings. Output [] if the plan is already complete."""


def _reachable(by_id: dict[str, Subtask], start: str) -> set[str]:
    """Transitive `depends_on` closure of `start` — everything it runs after."""
    seen: set[str] = set()
    stack = [start]
    while stack:
        node = stack.pop()
        for dep in by_id.get(node).depends_on if node in by_id else []:
            if dep not in seen:
                seen.add(dep)
                stack.append(dep)
    return seen


def structural_gaps(subtasks: list[Subtask]) -> list[str]:
    """Deterministic completeness/coherence gaps (no model call)."""
    gaps: list[str] = []
    by_id = {s.id: s for s in subtasks}

    dependents: dict[str, list[str]] = {s.id: [] for s in subtasks}
    for s in subtasks:
        for d in s.depends_on:
            if d in dependents:
                dependents[d].append(s.id)

    # A subtask others depend on must declare the interface they'll call (else drift — gap #2).
    for s in subtasks:
        if dependents[s.id] and not s.provides:
            gaps.append(
                f"{s.id} is depended on by {', '.join(dependents[s.id])} but declares no "
                f"`provides` — dependents can't be given its interface and may drift.")

    # The same file edited by subtasks with no ordering between them → conflicting concurrent edits.
    owners: dict[str, list[str]] = {}
    for s in subtasks:
        for f in s.target_files:
            owners.setdefault(f, []).append(s.id)
    for f, ids in owners.items():
        if len(ids) < 2:
            continue
        unordered = [
            (a, b) for i, a in enumerate(ids) for b in ids[i + 1:]
            if b not in _reachable(by_id, a) and a not in _reachable(by_id, b)
        ]
        if unordered:
            gaps.append(
                f"file '{f}' is edited by unordered subtasks {', '.join(sorted(set(ids)))} — "
                f"add `depends_on` to sequence them, or they will conflict.")
    return gaps


def completeness_review(task: str, subtasks: list[Subtask], router,
                        role: str = "planner") -> tuple[list[str], dict]:
    """Model-based goal-backward gap analysis. Returns (missing_steps, call_meta). Best-effort."""
    listing = "\n".join(
        f"- {s.id}: {s.description} (provides: {', '.join(s.provides) or 'none'})"
        for s in subtasks)
    user = (f"GOAL:\n{task}\n\nPLANNED SUBTASKS (in order):\n{listing}\n\n"
            f"List the REQUIRED missing steps as a JSON array.")
    try:
        result = router.complete(role, PLAN_CHECK_SYSTEM, user, max_tokens=600,
                                 cacheable_system=True)
    except Exception:  # noqa: BLE001 — completeness review is best-effort, never fails a run
        return [], {}
    arr = _extract_json_array(result.text)
    gaps = [str(x).strip() for x in arr if str(x).strip()] if isinstance(arr, list) else []
    meta = {"model": result.model_name, "tier": result.tier, "tokens_in": result.tokens_in,
            "tokens_out": result.tokens_out, "cost_usd": result.cost_usd}
    return gaps, meta
