"""Escalation — triggered by a GATE FAILURE, never by self-reported confidence.

When the local model's output fails the deterministic gate (after a retry), compress the
failure context and ask the REVIEWER role (a frontier model) for corrected guidance — a short
plan, not code. The local executor then re-runs with that guidance. Cheap, because the
frontier model sees a compressed payload and emits only instructions."""
from __future__ import annotations

from ..context.compress import compress_for_frontier
from ..context.retrieve import ContextBundle
from ..decompose.planner import Subtask
from ..models.router import Router

REVIEWER_SYSTEM = """\
You are a senior engineer. A junior model attempted a change and it FAILED automated checks
(types/tests/lint/security). You are given a compressed context, the task, the junior's
attempt, and the failure output.

Output SHORT, concrete corrective guidance (max ~10 bullet points) the junior should follow to
fix it. Do NOT write the full implementation — give precise instructions, signatures, and the
specific mistakes to correct.
"""


def get_correction(
    subtask: Subtask,
    bundle: ContextBundle,
    attempt_text: str,
    gate_report: str,
    router: Router,
) -> tuple[str, str | None, str | None, int, int, float]:
    """Returns (guidance, model_name, tier, tokens_in, tokens_out, cost_usd)."""
    compressed = compress_for_frontier(bundle.views, router=None)  # deterministic only — keep it tight
    user = (
        f"TASK:\n{subtask.description}\n\n"
        f"COMPRESSED CONTEXT (contracts only):\n{compressed[:6000]}\n\n"
        f"JUNIOR ATTEMPT (edit blocks):\n{attempt_text[:3000]}\n\n"
        f"FAILURE OUTPUT:\n{gate_report[:3000]}\n\n"
        f"Give corrective guidance."
    )
    result = router.complete("reviewer", REVIEWER_SYSTEM, user, max_tokens=900, cacheable_system=True)
    return (result.text, result.model_name, result.tier,
            result.tokens_in, result.tokens_out, result.cost_usd)
