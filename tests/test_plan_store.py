import pytest

from devagent.decompose.planner import Plan, Subtask
from devagent.planning import plan_store


def _plan():
    return Plan(
        subtasks=[
            Subtask("s1", "create model", ["m.py"], []),
            Subtask("s2", "wire api", ["api.py"], ["s1"]),
        ],
        decomposed=True, planner_model="claude-cli-opus",
    )


def test_save_then_load_round_trip(tmp_path):
    pid, path = plan_store.save_plan(tmp_path, "build the thing", _plan())
    assert path.exists()
    task, plan = plan_store.load_plan(tmp_path, pid)
    assert task == "build the thing"
    assert [s.id for s in plan.subtasks] == ["s1", "s2"]
    assert plan.subtasks[1].depends_on == ["s1"]
    assert plan.planner_model == "claude-cli-opus"


def test_load_by_explicit_path(tmp_path):
    _, path = plan_store.save_plan(tmp_path, "t", _plan(), plan_id="myplan")
    task, plan = plan_store.load_plan(tmp_path, str(path))
    assert len(plan.subtasks) == 2


def test_hand_edited_plan_is_honored(tmp_path):
    """A user editing the YAML (e.g. removing a subtask) changes what `run --from-plan` executes."""
    pid, path = plan_store.save_plan(tmp_path, "t", _plan())
    import yaml
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["subtasks"] = data["subtasks"][:1]  # drop s2
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    _, plan = plan_store.load_plan(tmp_path, pid)
    assert [s.id for s in plan.subtasks] == ["s1"]


def test_list_plans(tmp_path):
    plan_store.save_plan(tmp_path, "a", _plan(), plan_id="p1")
    plan_store.save_plan(tmp_path, "b", _plan(), plan_id="p2")
    ids = {p["id"] for p in plan_store.list_plans(tmp_path)}
    assert ids == {"p1", "p2"}


def test_missing_plan_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        plan_store.load_plan(tmp_path, "nope")
