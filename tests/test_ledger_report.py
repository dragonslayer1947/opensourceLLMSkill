from dataclasses import dataclass

from devagent import ledger, report
from devagent.config import Pricing


@dataclass
class Call:
    model: str
    tier: str
    tin: int
    tout: int
    cost_usd: float = 0.0


PRICING = {"local": Pricing(0, 0), "sonnet": Pricing(3, 15), "opus": Pricing(15, 75)}


def test_billing_local_only(tmp_path):
    calls = [Call("local", "local", 1000, 1000)]
    actual, counter = report.billing(calls, PRICING, local_ref="sonnet")
    assert actual == 0.0
    # 1000 in * $3/M + 1000 out * $15/M = 0.003 + 0.015
    assert round(counter, 6) == round(0.003 + 0.015, 6)


def test_billing_cli_uses_reported_cost(tmp_path):
    calls = [Call("cli", "cli", 10, 10, cost_usd=0.05), Call("local", "local", 1000, 0)]
    actual, counter = report.billing(calls, PRICING, local_ref="sonnet")
    assert actual == 0.0                       # subscription + local = $0 marginal
    assert round(counter, 6) == round(0.05 + 0.003, 6)  # cli reported + local priced at sonnet


def test_billing_api_tier_charges(tmp_path):
    calls = [Call("opus", "frontier", 1000, 1000)]
    actual, counter = report.billing(calls, PRICING, local_ref="sonnet")
    assert actual == counter                    # API tier: actual == counterfactual
    assert round(actual, 6) == round(0.015 + 0.075, 6)


def test_ledger_log_and_totals(tmp_path):
    db = tmp_path / "t.db"
    ledger.log_task(db, {
        "session_id": "s", "task": "t", "files": ["a.py"], "models_used": ["local"],
        "tokens_in": 10, "tokens_out": 5, "actual_cost": 0.0, "counterfactual_cost": 0.02,
        "savings": 0.02, "quality_gate": {"syntax": "pass"}, "in_envelope": 1,
        "decomposed": 0, "n_subtasks": 1, "audit_result": None, "status": "applied",
    })
    t = ledger.totals(db)
    assert t["n"] == 1 and round(t["savings"], 4) == 0.02 and t["applied"] == 1


def test_ledger_audits_and_buckets(tmp_path):
    db = tmp_path / "t.db"
    rid = "cal-1"
    rows = [(1500, "local_better"), (1800, "equivalent"),
            (4000, "frontier_better"), (5000, "frontier_better")]
    for ctx, verdict in rows:
        ledger.log_audit(db, {
            "run_kind": "calibrate", "run_id": rid, "task": "t", "repo": ".",
            "context_tokens": ctx, "max_file_lines": 10, "verdict": verdict,
            "local_model": "local", "frontier_model": "cli", "judge_model": "cli", "reason": "r",
        })
    summary = ledger.audits_summary(db, rid)
    assert summary == {"scored": 4, "parity": 2}
    buckets = ledger.audits_by_bucket(db, rid, [(0, 2000), (2000, 6000)])
    assert buckets[0] == {"lo": 0, "hi": 2000, "total": 2, "parity": 2}
    assert buckets[1] == {"lo": 2000, "hi": 6000, "total": 2, "parity": 0}
