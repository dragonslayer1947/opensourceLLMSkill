"""Auto-provides: the Claude planner must EMIT `provides` for each subtask, so every
decomposition (not just hand-authored plan.json) gets interface-drift prevention."""
from dataclasses import dataclass

from devagent.context.index import build_index
from devagent.context.retrieve import retrieve
from devagent.decompose.planner import PLANNER_SYSTEM, decompose


@dataclass
class _Result:
    text: str
    model_name: str = "fake-planner"
    tier: str = "cli"
    tokens_in: int = 10
    tokens_out: int = 20
    cost_usd: float = 0.0


class _FakeRouter:
    """Returns a fixed plan that declares interfaces in `provides`."""
    def __init__(self, text):
        self._text = text

    def complete(self, role, system, user, **k):
        self._last_system = system
        return _Result(self._text)


def test_planner_system_prompt_requires_provides():
    assert "provides" in PLANNER_SYSTEM
    assert "{max_files}" in PLANNER_SYSTEM  # still a format template


def test_decompose_parses_provides(tmp_repo):
    idx = build_index(tmp_repo)
    b = retrieve(idx, "big multi-file feature", max_context_tokens=12000, max_file_lines=400)
    plan_json = (
        '[{"id":"s1","description":"add OrderRepo",'
        '"target_files":["app/repo.py"],"depends_on":[],'
        '"provides":["class OrderRepo with create(order: Order) -> Order in app/repo.py"]},'
        '{"id":"s2","description":"wire api","target_files":["app/api.py"],'
        '"depends_on":["s1"],"provides":[]}]'
    )
    plan = decompose("build orders", idx, b, _FakeRouter(plan_json),
                     max_subtask_files=3, force_decompose=True)
    assert plan.decomposed is True
    s1 = next(s for s in plan.subtasks if s.id == "s1")
    assert s1.provides == ["class OrderRepo with create(order: Order) -> Order in app/repo.py"]
    # missing/empty provides must default to [] cleanly, not crash
    s2 = next(s for s in plan.subtasks if s.id == "s2")
    assert s2.provides == []
