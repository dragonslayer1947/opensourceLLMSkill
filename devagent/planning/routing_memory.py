"""Self-tuning routing from observed parity (gap #6).

devagent already collects differential-audit verdicts (code via prove/audit.py, plans via
prove/plan_audit.py) but nothing acts on them — the frontier/local split is static. This closes the
loop: before trusting the local model for a context size, consult the recorded parity for similar
contexts. If the data says local under-performs there (parity rate below target with enough
samples), route to the frontier; otherwise keep the cheap default. With no data, it defers to the
default (local-first) — it only ever *restricts* local where evidence says to."""
from __future__ import annotations

from pathlib import Path

from .. import ledger


def advise_local(db_path: Path, ctx_tokens: int, *, parity_target: float = 0.9,
                 run_kind: str | None = None, min_samples: int = 5) -> tuple[bool, str]:
    """Should we trust the LOCAL model at this context size? Returns (prefer_local, reason).

    Bucketed around ctx_tokens (half..double) so the evidence is from comparable tasks. Below
    `min_samples` audits → defer to the default (True). Otherwise prefer local only if the measured
    parity rate meets the target."""
    lo, hi = max(0, ctx_tokens // 2), max(1, ctx_tokens * 2)
    stats = ledger.parity_stats(db_path, run_kind=run_kind, ctx_lo=lo, ctx_hi=hi)
    scored = stats["scored"]
    if scored < min_samples:
        return True, f"insufficient parity data ({scored} audits) — default local-first"
    rate = stats["parity"] / scored
    if rate >= parity_target:
        return True, f"measured local parity {rate:.0%} ≥ target {parity_target:.0%} → local"
    return False, (f"measured local parity {rate:.0%} < target {parity_target:.0%} "
                   f"({scored} audits) → frontier")
