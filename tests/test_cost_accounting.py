"""Honest end-to-end cost accounting (gap #5): host orchestration is reported separately and
never inflates the execution savings."""
from devagent import ledger, report
from devagent.config import Pricing
from devagent.pipeline import Call

PRICING = {"sonnet": Pricing(3.0, 15.0)}


def test_billing_skips_host():
    calls = [Call("local", "local", 1000, 1000, 0.0),
             Call("sonnet", "host", 2000, 2000, 0.05)]
    actual, counter = report.billing(calls, PRICING, "sonnet")
    assert actual == 0.0
    # only the local execution is priced into the counterfactual; host is excluded
    assert round(counter, 6) == round((1000 * 3.0 + 1000 * 15.0) / 1_000_000, 6)


def test_summary_separates_host_and_keeps_savings_clean():
    calls = [Call("local", "local", 1000, 1000, 0.0),
             Call("sonnet", "host", 2000, 2000, 0.05)]
    s = report.summary(calls, PRICING, "sonnet")
    assert s["host_measured"] is True
    assert s["host_tokens"] == 4000 and s["host_cost"] == 0.05
    assert s["exec_tokens"] == 2000 and s["local_tokens"] == 2000
    assert s["pct_local_exec"] == 100.0           # all execution ran local
    assert round(s["pct_local_end2end"]) == 33    # but only 2000/6000 of ALL model work
    assert s["actual"] == 0.0
    assert s["savings"] == s["counterfactual"]     # host nets out of savings entirely


def test_summary_no_host_is_fully_local():
    calls = [Call("local", "local", 100, 100, 0.0)]
    s = report.summary(calls, PRICING, "sonnet")
    assert s["host_measured"] is False and s["host_tokens"] == 0
    assert s["pct_local_exec"] == 100.0 and s["pct_local_end2end"] == 100.0


def test_ledger_host_cost_migration_and_totals(tmp_path):
    db = tmp_path / "tasks.db"
    ledger.log_task(db, {"session_id": "s1", "task": "t", "actual_cost": 0.0,
                         "counterfactual_cost": 1.0, "savings": 1.0, "host_cost": 0.07,
                         "in_envelope": 1, "status": "applied"})
    t = ledger.totals(db)
    assert round(t["host_cost"], 4) == 0.07
    assert round(t["savings"], 4) == 1.0   # savings independent of host_cost
