import json
from dataclasses import dataclass

from devagent.longhorizon import epic as epic_mod


@dataclass
class FakeResult:
    text: str
    model_name: str = "fake-planner"
    tier: str = "cli"
    tokens_in: int = 10
    tokens_out: int = 20
    cost_usd: float = 0.0


class FakeRouter:
    def __init__(self, text):
        self.text = text
        self.calls = 0

    def complete(self, *a, **k):
        self.calls += 1
        return FakeResult(self.text)


PLAN_JSON = {
    "title": "Checkout v2",
    "preconditions": ["payments service reachable"],
    "postconditions": ["users can check out"],
    "stories": [
        {"id": "S1", "title": "Cart", "tasks": [
            {"id": "T1", "title": "add cart model", "target_files": ["cart.py"]},
            {"id": "T2", "title": "cart api", "target_files": ["api.py"], "depends_on": ["T1"]},
        ]},
        {"id": "S2", "title": "Pay", "tasks": [
            {"id": "T3", "title": "charge", "target_files": ["pay.py"], "depends_on": ["T2"]},
        ]},
    ],
}


def test_build_epic_flattens_and_namespaces():
    epic = epic_mod.build_epic("E-0001", "build checkout", PLAN_JSON)
    assert epic.root.kind == "epic"
    assert len(epic.stories()) == 2
    tasks = epic.tasks()
    assert len(tasks) == 3
    # ids are namespaced under the epic
    assert all(t.id.startswith("E-0001.") for t in tasks)
    # depends_on remapped to namespaced ids
    t2 = next(t for t in tasks if t.title == "cart api")
    assert t2.depends_on == ["E-0001.S1.T1"]


def test_decompose_epic_with_router():
    router = FakeRouter(json.dumps(PLAN_JSON))
    epic = epic_mod.decompose_epic("E-0001", "build checkout", router, max_subtask_files=3)
    assert router.calls == 1
    assert epic.decomposed and len(epic.tasks()) == 3
    assert epic.planner_model == "fake-planner"


def test_decompose_epic_garbage_falls_back_to_single_task():
    epic = epic_mod.decompose_epic("E-0009", "do thing", FakeRouter("not json"),
                                   max_subtask_files=3)
    assert len(epic.tasks()) == 1
    assert epic.tasks()[0].description == "do thing"


def test_save_and_load_round_trip(tmp_path):
    epic = epic_mod.build_epic("E-0001", "build checkout", PLAN_JSON)
    epic_mod.save_epic(tmp_path, epic)
    loaded = epic_mod.load_epic(tmp_path, "E-0001")
    assert loaded is not None
    assert loaded.goal == "build checkout"
    assert [t.id for t in loaded.tasks()] == [t.id for t in epic.tasks()]


def test_next_epic_id_increments(tmp_path):
    assert epic_mod.next_epic_id(tmp_path) == "E-0001"
    epic_mod.save_epic(tmp_path, epic_mod.build_epic("E-0001", "g", PLAN_JSON))
    assert epic_mod.next_epic_id(tmp_path) == "E-0002"


def test_list_epics(tmp_path):
    epic_mod.save_epic(tmp_path, epic_mod.build_epic("E-0001", "a", PLAN_JSON))
    epic_mod.save_epic(tmp_path, epic_mod.build_epic("E-0002", "b", PLAN_JSON))
    assert {e.id for e in epic_mod.list_epics(tmp_path)} == {"E-0001", "E-0002"}
