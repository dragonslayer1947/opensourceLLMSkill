from dataclasses import dataclass, field

from devagent.planning.scheduler import max_parallelism, schedule


@dataclass
class ST:
    id: str
    target_files: list = field(default_factory=list)
    depends_on: list = field(default_factory=list)


def _ids(waves):
    return [sorted(s.id for s in w) for w in waves]


def test_disjoint_files_run_in_one_wave():
    subs = [ST("a", ["a.py"]), ST("b", ["b.py"]), ST("c", ["c.py"])]
    waves = schedule(subs)
    assert _ids(waves) == [["a", "b", "c"]]
    assert max_parallelism(waves) == 3


def test_file_conflict_splits_waves():
    subs = [ST("a", ["shared.py"]), ST("b", ["shared.py"])]
    waves = schedule(subs)
    assert len(waves) == 2 and [len(w) for w in waves] == [1, 1]


def test_dependencies_respected():
    subs = [ST("a", ["a.py"]), ST("b", ["b.py"], depends_on=["a"])]
    waves = schedule(subs)
    order = _ids(waves)
    assert order[0] == ["a"] and order[1] == ["b"]


def test_empty_files_runs_alone():
    subs = [ST("a", []), ST("b", ["b.py"])]
    waves = schedule(subs)
    # the no-footprint subtask must be alone in its wave
    for w in waves:
        if any(s.id == "a" for s in w):
            assert len(w) == 1


def test_cycle_does_not_deadlock():
    subs = [ST("a", ["a.py"], depends_on=["b"]), ST("b", ["b.py"], depends_on=["a"])]
    waves = schedule(subs)
    scheduled = {s.id for w in waves for s in w}
    assert scheduled == {"a", "b"}  # forced progress, no infinite loop


def test_all_subtasks_scheduled_once():
    subs = [ST(f"s{i}", [f"f{i}.py"]) for i in range(5)]
    waves = schedule(subs)
    flat = [s.id for w in waves for s in w]
    assert sorted(flat) == sorted(s.id for s in subs) and len(flat) == len(set(flat))
