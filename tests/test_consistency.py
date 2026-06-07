import io

from rich.console import Console

from devagent.decompose.planner import Plan
from devagent.pipeline import RunResult, SubtaskOutcome, _consistency_check


def _result(outcomes):
    r = RunResult(session_id="s", plan=Plan([], False, None))
    r.outcomes = outcomes
    return r


def _oc(sid, files, status="applied"):
    return SubtaskOutcome(sid, sid, files, {}, False, status)


def test_no_conflict_when_disjoint():
    buf = io.StringIO()
    _consistency_check(_result([_oc("a", ["a.py"]), _oc("b", ["b.py"])]), Console(file=buf))
    assert "multiple subtasks" not in buf.getvalue()


def test_conflict_detected_on_overlap():
    buf = io.StringIO()
    _consistency_check(_result([_oc("a", ["shared.py"]), _oc("b", ["shared.py"])]), Console(file=buf))
    out = buf.getvalue()
    assert "multiple subtasks" in out and "shared.py" in out


def test_non_applied_ignored():
    buf = io.StringIO()
    _consistency_check(
        _result([_oc("a", ["x.py"]), _oc("b", ["x.py"], status="gate_failed")]), Console(file=buf))
    assert "multiple subtasks" not in buf.getvalue()
