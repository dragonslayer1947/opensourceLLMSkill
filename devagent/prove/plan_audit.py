"""Plan-parity audit (gap #2) — measure whether LOCAL decomposition holds up vs the frontier.

Local-first planning trusts the local model to decompose, gated only by structural checks. But a
structurally-valid plan can still be semantically worse (missing a step, wrong ordering). This is
the differential audit for PLANS, mirroring prove/audit.py for code: decompose the same goal with
the local model and the frontier model, then a blinded judge compares completeness/correctness of
the breakdown. Aggregated, this tells you WHICH tasks you can safely plan locally — turning the
local-first assumption into a measured parity rate. The frontier plan is for comparison only."""
from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path

from ..config import Config
from ..context.cache import build_index_cached
from ..context.retrieve import retrieve
from ..decompose.planner import (PLANNER_SYSTEM, _extract_json_array, _repo_skeleton,
                                 _subs_from_arr)
from ..models.registry import Registry
from ..models.router import Router
from .audit import _parse_winner

PLAN_JUDGE_SYSTEM = """\
You compare two DECOMPOSITIONS, A and B, of the SAME coding goal into ordered subtasks. Judge which
breakdown better ACHIEVES the goal: completeness (no required step missing — wiring, tests,
migrations), correct ordering/dependencies, and right-sized steps. Ignore wording.

Output ONLY JSON: {"winner": "A" | "B" | "tie", "reason": "<= 2 sentences"}."""


@dataclass
class PlanAuditResult:
    task: str
    repo: str
    verdict: str            # local_better | equivalent | frontier_better | skipped
    reason: str
    local_model: str | None = None
    frontier_model: str | None = None


def _plan_text(subtasks) -> str:
    return "\n".join(
        f"- {s.id}: {s.description} (files: {', '.join(s.target_files) or 'n/a'}; "
        f"deps: {', '.join(s.depends_on) or 'none'})" for s in subtasks)


def plan_audit(task: str, repo: str, config: Config, registry: Registry, router: Router,
               *, run_id: str | None = None) -> PlanAuditResult:
    root = Path(repo).resolve()
    index = build_index_cached(root)
    bundle = retrieve(index, task,
                      max_context_tokens=int(config.envelope.get("max_context_tokens", 12000)),
                      max_file_lines=int(config.envelope.get("max_file_lines", 400)))

    fname = config.reporting.get("counterfactual_model", "opus")
    fclient = registry.get(fname)
    if fclient is None or fclient.is_local:
        return PlanAuditResult(task, repo, "skipped",
                               f"frontier model '{fname}' unavailable for comparison")

    max_files = int(config.envelope.get("max_subtask_files", 3))
    skeleton = _repo_skeleton(index, bundle.candidate_files)
    system = PLANNER_SYSTEM.format(max_files=max_files)
    user = (f"TASK:\n{task}\n\nRELEVANT FILES (signatures only):\n"
            f"{skeleton or '(no indexed candidates)'}\n\nDecompose into the smallest safe ordered steps.")

    def _plan(role_or_client):
        if role_or_client == "local":
            res = router.complete("planner_local", system, user, max_tokens=1500)
            return _subs_from_arr(_extract_json_array(res.text) or [], task, bundle), res.model_name
        res = fclient.complete(system, user, max_tokens=1500)
        return _subs_from_arr(_extract_json_array(res.text) or [], task, bundle), fname

    local_subs, lmodel = _plan("local")
    frontier_subs, _ = _plan(fclient)
    if not local_subs or not frontier_subs:
        return PlanAuditResult(task, repo, "skipped", "one side produced no parseable plan",
                               local_model=lmodel, frontier_model=fname)

    swap = random.random() < 0.5
    a, b = ((frontier_subs, local_subs) if swap else (local_subs, frontier_subs))
    judge_user = (f"GOAL:\n{task}\n\n--- DECOMPOSITION A ---\n{_plan_text(a)}\n\n"
                  f"--- DECOMPOSITION B ---\n{_plan_text(b)}\n\nWhich better achieves the goal? JSON only.")
    jres = fclient.complete(PLAN_JUDGE_SYSTEM, judge_user, max_tokens=300, cacheable_system=True)
    winner = _parse_winner(jres.text)

    if winner == "tie" or winner is None:
        verdict = "equivalent"
    else:
        local_is_a = not swap
        local_won = (winner == "a" and local_is_a) or (winner == "b" and not local_is_a)
        verdict = "local_better" if local_won else "frontier_better"
    return PlanAuditResult(task, repo, verdict, (jres.text or "").strip()[:300],
                           local_model=lmodel, frontier_model=fname)


def persist(db_path: Path, result: PlanAuditResult, *, run_id: str | None = None) -> None:
    from .. import ledger
    ledger.log_audit(db_path, {
        "run_kind": "plan_audit", "run_id": run_id, "task": result.task, "repo": result.repo,
        "context_tokens": 0, "max_file_lines": 0, "verdict": result.verdict,
        "local_model": result.local_model, "frontier_model": result.frontier_model,
        "judge_model": result.frontier_model, "reason": result.reason,
    })
