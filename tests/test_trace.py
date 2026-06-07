from devagent.observability import trace as trace_mod


def test_record_and_save_load(tmp_path):
    tr = trace_mod.new_trace("20260607-101010", "add endpoint")
    tr.record("routing", route="plan_execute", score=7, reasons=["large"])
    tr.record("blast_radius", score=3, level="medium", affected=3)
    tr.record("subtask", id="s1", status="applied", cost_usd=0.01, duration_s=1.2)
    tr.record("final", status="applied", actual_cost=0.0)
    p = tr.save(tmp_path)
    assert p and p.exists()

    data = trace_mod.load_trace(tmp_path, "20260607-101010")
    assert data["task"] == "add endpoint"
    kinds = [e["kind"] for e in data["events"]]
    assert kinds == ["routing", "blast_radius", "subtask", "final"]


def test_latest_and_list(tmp_path):
    trace_mod.new_trace("20260607-090000").save(tmp_path)
    trace_mod.new_trace("20260607-100000").save(tmp_path)
    assert trace_mod.list_traces(tmp_path) == ["20260607-090000", "20260607-100000"]
    assert trace_mod.latest(tmp_path) == "20260607-100000"


def test_summarize_rolls_up_cost_and_blast(tmp_path):
    tr = trace_mod.new_trace("s1")
    tr.record("blast_radius", score=2, level="low", affected=2)
    tr.record("subtask", id="a", status="applied", cost_usd=0.02)
    tr.record("subtask", id="b", status="applied", cost_usd=0.03)
    summ = trace_mod.summarize(tr.to_dict())
    assert summ["total_cost"] == 0.05
    assert summ["blast_radius"]["level"] == "low"
    assert len(summ["subtasks"]) == 2


def test_missing_trace_returns_none(tmp_path):
    assert trace_mod.load_trace(tmp_path, "nope") is None
    assert trace_mod.latest(tmp_path) is None
