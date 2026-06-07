"""Measurement/honesty improvements: gate strength (#3), host estimate (#1), retrieval eval (#5),
plan-parity audit basics (#2), self-tuning routing (#6)."""
from devagent import ledger, report
from devagent.context.index import build_index
from devagent.planning import routing_memory
from devagent.prove import plan_audit, retrieval_eval
from devagent.validate.gate import gate_strength


def _w(root, rel, text):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


# ── #3 gate strength ──
def test_gate_strength_reports_reduced_floor():
    gs = gate_strength({"run_types": False, "run_lint": False, "run_security": False})
    assert gs["active"] == ["syntax"]
    assert gs["floor"] == "weak"          # types + security off => weak floor
    assert any(s.startswith("security") for s in gs["skipped"])


def test_gate_strength_syntax_always_active():
    assert "syntax" in gate_strength({})["active"]


# ── #1 host overhead estimate ──
def test_host_overhead_estimate_positive_and_monotonic():
    a_in, a_out = report.estimate_host_overhead(1, 1)
    b_in, b_out = report.estimate_host_overhead(5, 6)
    assert a_in > 0 and a_out > 0
    assert b_in > a_in and b_out > a_out      # more subtasks/files => more host work


# ── #5 retrieval eval ──
def test_retrieval_self_recall(tmp_path):
    _w(tmp_path, "app/payments.py", "def charge_card():\n    return 1\n\ndef refund_card():\n    return 2\n")
    _w(tmp_path, "app/auth.py", "def login_user():\n    return 1\n\ndef logout_user():\n    return 2\n")
    _w(tmp_path, "app/reports.py", "def build_report():\n    return 1\n")
    idx = build_index(tmp_path)
    res = retrieval_eval.evaluate(idx, k=3)
    assert res.n == 3
    assert res.recall_at_k >= 0.66      # each file is findable by its own symbols
    assert 0.0 < res.mrr <= 1.0


# ── #2 plan-parity audit (skip path is deterministic) ──
def test_plan_audit_skips_without_frontier(tmp_repo, make_config):
    from devagent.models.registry import Registry
    from devagent.models.router import Router
    cfg = make_config()
    cfg.reporting["counterfactual_model"] = "nonexistent-model"
    reg = Registry(cfg)
    res = plan_audit.plan_audit("do a thing", str(tmp_repo), cfg, reg, Router(reg))
    assert res.verdict == "skipped"


def test_plan_text_lists_subtasks():
    from devagent.decompose.planner import Subtask
    txt = plan_audit._plan_text([Subtask(id="s1", description="add repo", target_files=["a.py"])])
    assert "s1" in txt and "add repo" in txt and "a.py" in txt


# ── #6 self-tuning routing ──
def _seed(db, verdict, n, ctx=8000):
    for i in range(n):
        ledger.log_audit(db, {"run_kind": "plan_audit", "verdict": verdict, "context_tokens": ctx,
                              "task": f"t{i}", "repo": "r", "max_file_lines": 0})


def test_routing_defers_to_local_without_data(tmp_path):
    ok, why = routing_memory.advise_local(tmp_path / "x.db", 8000, run_kind="plan_audit")
    assert ok and "insufficient" in why


def test_routing_keeps_local_when_parity_high(tmp_path):
    db = tmp_path / "x.db"
    _seed(db, "local_better", 6, ctx=8000)
    ok, why = routing_memory.advise_local(db, 8000, parity_target=0.9, run_kind="plan_audit")
    assert ok and "≥ target" in why


def test_routing_escalates_when_parity_low(tmp_path):
    db = tmp_path / "x.db"
    _seed(db, "frontier_better", 6, ctx=8000)   # local lost every time
    ok, why = routing_memory.advise_local(db, 8000, parity_target=0.9, run_kind="plan_audit")
    assert not ok and "< target" in why
