"""Differential quality audit — the proof that local-model output holds quality.

Run the SAME task through the local executor and the frontier model (same prompt, same
context), then a blinded judge compares the two. The verdict is recorded. Aggregated over many
tasks (see calibrate), this is the measured parity rate reported by `devagent quality`.

The frontier output here is generated ONLY for comparison — it is never applied to the repo.
Honest caveat: the judge is an LLM (position/verbosity bias); blinding mitigates it but the
deterministic gate, not this, is the quality floor."""
from __future__ import annotations

import random
import re
from dataclasses import dataclass
from pathlib import Path

from ..config import Config
from ..context.compress import compress_for_frontier
from ..context.cache import build_index_cached
from ..context.retrieve import retrieve
from ..decompose.planner import Subtask
from ..execute.executor import build_executor_prompt, execute_subtask
from ..models.registry import Registry
from ..models.router import Router

JUDGE_SYSTEM = """\
You are a strict senior code reviewer comparing two candidate implementations, A and B, for the
SAME task. Judge in this priority order: correctness, completeness, then code quality and fit
with the surrounding code's conventions.

Output ONLY JSON: {"winner": "A" | "B" | "tie", "reason": "<= 2 sentences"}.
"""


@dataclass
class AuditResult:
    task: str
    repo: str
    context_tokens: int
    max_file_lines: int
    verdict: str           # local_better | equivalent | frontier_better | skipped
    reason: str
    local_model: str | None = None
    frontier_model: str | None = None
    judge_model: str | None = None


def _parse_winner(text: str) -> str | None:
    m = re.search(r'"winner"\s*:\s*"(A|B|tie)"', text, re.IGNORECASE)
    if m:
        return m.group(1).lower()
    m = re.search(r"\b(tie|A|B)\b", text.strip())
    return m.group(1).lower() if m else None


def _max_file_lines(index, candidate_files: list[str]) -> int:
    by_rel = {f.rel: f for f in index.files}
    sizes = [by_rel[r].lines for r in candidate_files if r in by_rel]
    return max(sizes) if sizes else 0


def differential_audit(
    task: str,
    repo: str,
    config: Config,
    registry: Registry,
    router: Router,
    *,
    run_kind: str = "audit",
    run_id: str | None = None,
    local_raw: str | None = None,
    local_model: str | None = None,
) -> AuditResult:
    root = Path(repo).resolve()
    index = build_index_cached(root)
    bundle = retrieve(
        index, task,
        max_context_tokens=int(config.envelope.get("max_context_tokens", 12000)),
        max_file_lines=int(config.envelope.get("max_file_lines", 400)),
    )
    ctx_tokens = bundle.est_tokens
    max_lines = _max_file_lines(index, bundle.candidate_files)

    # Frontier model must be a real, available, non-local model — else the audit is meaningless.
    fname = config.reporting.get("counterfactual_model", "opus")
    fclient = registry.get(fname)
    if fclient is None or fclient.is_local:
        return AuditResult(task, repo, ctx_tokens, max_lines, "skipped",
                           f"frontier model '{fname}' unavailable ({registry.error_for(fname) or 'local fallback'})")

    subtask = Subtask("s1", task, bundle.candidate_files[:1])

    # Local candidate (reuse a prior run's output if provided).
    if local_raw is None:
        lout = execute_subtask(subtask, bundle, router, role="executor")
        local_raw, local_model = lout.raw, lout.model

    # Frontier candidate — same prompt, generated for comparison only (never applied).
    system, user = build_executor_prompt(subtask, bundle)
    fres = fclient.complete(system, user)
    frontier_raw = fres.text

    # Blinded judge.
    swap = random.random() < 0.5
    cand_a, cand_b = (frontier_raw, local_raw) if swap else (local_raw, frontier_raw)
    ctx = compress_for_frontier(bundle.views, router=None)
    judge_user = (
        f"TASK:\n{task}\n\n"
        f"CONTEXT (contracts):\n{ctx[:4000]}\n\n"
        f"--- CANDIDATE A ---\n{cand_a[:4000]}\n\n"
        f"--- CANDIDATE B ---\n{cand_b[:4000]}\n\n"
        f"Which better implements the task? Respond with the JSON only."
    )
    jres = fclient.complete(JUDGE_SYSTEM, judge_user, max_tokens=300, cacheable_system=True)
    winner = _parse_winner(jres.text)

    # De-blind: map A/B back to local/frontier.
    if winner == "tie" or winner is None:
        verdict = "equivalent"
    else:
        local_is_a = not swap
        local_won = (winner == "a" and local_is_a) or (winner == "b" and not local_is_a)
        verdict = "local_better" if local_won else "frontier_better"

    return AuditResult(
        task=task, repo=repo, context_tokens=ctx_tokens, max_file_lines=max_lines,
        verdict=verdict, reason=(jres.text or "").strip()[:300],
        local_model=local_model, frontier_model=fname, judge_model=fname,
    )


def persist(db_path: Path, result: AuditResult, *, run_kind: str, run_id: str | None) -> None:
    from .. import ledger
    ledger.log_audit(db_path, {
        "run_kind": run_kind, "run_id": run_id, "task": result.task, "repo": result.repo,
        "context_tokens": result.context_tokens, "max_file_lines": result.max_file_lines,
        "verdict": result.verdict, "local_model": result.local_model,
        "frontier_model": result.frontier_model, "judge_model": result.judge_model,
        "reason": result.reason,
    })
