"""Local-first planning (#2) + plan caching (#4): decompose on the LOCAL model, escalate to the
frontier only when the local plan is weak, and reuse a cached plan for an identical task."""
from dataclasses import dataclass

from devagent.context.index import build_index
from devagent.context.retrieve import retrieve
from devagent.decompose import planner
from devagent.decompose.planner import _weak_plan, decompose
from devagent.decompose.planner import Subtask


@dataclass
class _R:
    text: str
    model_name: str
    tier: str
    tokens_in: int = 5
    tokens_out: int = 5
    cost_usd: float = 0.0


class RecordingRouter:
    """Returns a canned plan per role and records which roles were asked."""
    def __init__(self, by_role):
        self.by_role = by_role
        self.roles_called = []

    def complete(self, role, system, user, **k):
        self.roles_called.append(role)
        tier = "local" if role == "planner_local" else "cli"
        return _R(self.by_role.get(role, ""), f"model-{role}", tier)


_GOOD = '[{"id":"s1","description":"add repo","target_files":["a.py"],"depends_on":[]},' \
        '{"id":"s2","description":"wire api","target_files":["b.py"],"depends_on":["s1"]}]'
_WEAK = '[{"id":"s1","description":"too big","target_files":["a.py","b.py","c.py","d.py"]}]'


def _bundle(tmp_repo):
    idx = build_index(tmp_repo)
    return idx, retrieve(idx, "do a multi-file feature", max_context_tokens=12000, max_file_lines=400)


def test_weak_plan_detects_over_envelope():
    assert _weak_plan([Subtask(id="s1", description="x", target_files=["a", "b", "c", "d"])], 3)
    assert not _weak_plan([Subtask(id="s1", description="x", target_files=["a"])], 3)
    assert _weak_plan([], 3)  # empty plan is weak


def test_local_plan_used_without_frontier(tmp_repo):
    idx, b = _bundle(tmp_repo)
    r = RecordingRouter({"planner_local": _GOOD})
    plan = decompose("feature", idx, b, r, max_subtask_files=3, force_decompose=True, use_cache=False)
    assert r.roles_called == ["planner_local"]           # frontier NEVER consulted
    assert plan.decomposed and plan.planner_tier == "local" and len(plan.subtasks) == 2


def test_weak_local_plan_escalates_to_frontier(tmp_repo):
    idx, b = _bundle(tmp_repo)
    r = RecordingRouter({"planner_local": _WEAK, "planner": _GOOD})
    plan = decompose("feature", idx, b, r, max_subtask_files=3, force_decompose=True, use_cache=False)
    assert r.roles_called == ["planner_local", "planner"]   # escalated because local plan was weak
    assert plan.planner_model == "model-planner" and len(plan.subtasks) == 2


def test_frontier_plan_flag_skips_local(tmp_repo):
    idx, b = _bundle(tmp_repo)
    r = RecordingRouter({"planner": _GOOD})
    plan = decompose("feature", idx, b, r, max_subtask_files=3, force_decompose=True,
                     prefer_local=False, use_cache=False)
    assert r.roles_called == ["planner"] and plan.decomposed


def test_plan_cache_avoids_second_call(tmp_repo):
    idx, b = _bundle(tmp_repo)
    r1 = RecordingRouter({"planner_local": _GOOD})
    decompose("same feature", idx, b, r1, max_subtask_files=3, force_decompose=True, use_cache=True)
    assert r1.roles_called == ["planner_local"]
    # identical task + candidate files -> cache hit, NO router call at all
    r2 = RecordingRouter({"planner_local": _GOOD})
    plan2 = decompose("same feature", idx, b, r2, max_subtask_files=3, force_decompose=True,
                      use_cache=True)
    assert r2.roles_called == []
    assert plan2.planner_model == "(cached)" and len(plan2.subtasks) == 2
