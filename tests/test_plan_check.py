"""Goal-backward plan verification (Tier-1)."""
from dataclasses import dataclass

from devagent.decompose.planner import Subtask
from devagent.planning import plan_check


def test_structural_gap_dependents_without_provides():
    subs = [Subtask(id="s1", description="repo", target_files=["a.py"], provides=[]),
            Subtask(id="s2", description="api", target_files=["b.py"], depends_on=["s1"])]
    gaps = plan_check.structural_gaps(subs)
    assert any("s1 is depended on by s2" in g and "provides" in g for g in gaps)


def test_no_gap_when_provides_declared():
    subs = [Subtask(id="s1", description="repo", target_files=["a.py"], provides=["class Repo"]),
            Subtask(id="s2", description="api", target_files=["b.py"], depends_on=["s1"])]
    assert plan_check.structural_gaps(subs) == []


def test_structural_gap_unordered_file_overlap():
    subs = [Subtask(id="s1", description="x", target_files=["app/main.py"]),
            Subtask(id="s2", description="y", target_files=["app/main.py"])]  # both edit main, no dep
    gaps = plan_check.structural_gaps(subs)
    assert any("app/main.py" in g and "unordered" in g for g in gaps)


def test_no_gap_when_file_overlap_is_ordered():
    subs = [Subtask(id="s1", description="x", target_files=["app/main.py"], provides=["m"]),
            Subtask(id="s2", description="y", target_files=["app/main.py"], depends_on=["s1"])]
    assert plan_check.structural_gaps(subs) == []  # sequenced -> fine


class _Router:
    def __init__(self, text, boom=False):
        self.text = text
        self.boom = boom

    def complete(self, *a, **k):
        if self.boom:
            raise RuntimeError("planner down")

        @dataclass
        class R:
            text: str
            model_name: str = "planner"
            tier: str = "cli"
            tokens_in: int = 5
            tokens_out: int = 5
            cost_usd: float = 0.0
        return R(self.text)


def test_completeness_review_parses_missing_steps():
    subs = [Subtask(id="s1", description="add endpoint", target_files=["api.py"])]
    gaps, meta = plan_check.completeness_review("build feature", subs,
                                                _Router('["register the route", "add tests"]'))
    assert gaps == ["register the route", "add tests"] and meta["model"] == "planner"


def test_completeness_review_best_effort_on_failure():
    subs = [Subtask(id="s1", description="x", target_files=["a.py"])]
    gaps, meta = plan_check.completeness_review("t", subs, _Router("", boom=True))
    assert gaps == [] and meta == {}
