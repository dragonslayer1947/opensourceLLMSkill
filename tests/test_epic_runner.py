from devagent.longhorizon import epic as epic_mod
from devagent.longhorizon import runner

PLAN = {
    "title": "feature",
    "stories": [
        {"id": "S1", "title": "story one", "tasks": [
            {"id": "T1", "title": "first", "target_files": ["a.py"]},
            {"id": "T2", "title": "second", "target_files": ["b.py"], "depends_on": ["T1"]},
        ]},
    ],
}


def _epic():
    return epic_mod.build_epic("E-0001", "feature", PLAN)


def test_ready_respects_dependencies(tmp_path):
    epic = _epic()
    state = runner.init_state(epic)
    ready = runner.ready_tasks(epic, state)
    # only T1 is ready (T2 depends on T1)
    assert [t.title for t in ready] == ["first"]


def test_run_epic_completes_in_dependency_order(tmp_path):
    epic = _epic()
    order = []

    def execute(task):
        order.append(task.title)
        return True, "applied"

    summary = runner.run_epic(tmp_path, epic, execute)
    assert order == ["first", "second"]
    assert summary["done"] == 2 and summary["pct"] == 100


def test_failed_task_blocks_dependent(tmp_path):
    epic = _epic()

    def execute(task):
        return (task.title != "first"), "gate_failed" if task.title == "first" else "applied"

    summary = runner.run_epic(tmp_path, epic, execute)
    # T1 failed → T2 never becomes ready (dep not done)
    assert summary["failed"] == 1 and summary["done"] == 0


def test_checkpoint_and_resume(tmp_path):
    epic = _epic()
    epic_mod.save_epic(tmp_path, epic)

    # First session: only run one task.
    runner.run_epic(tmp_path, epic, lambda t: (True, "applied"), max_tasks=1)
    state = runner.load_state(tmp_path, epic)
    assert runner.progress(epic, state)["done"] == 1

    # Second session resumes from disk and finishes the rest.
    ran = []
    summary = runner.run_epic(tmp_path, epic, lambda t: (ran.append(t.id), (True, "ok"))[1])
    assert summary["done"] == 2          # total done across sessions
    assert len(ran) == 1                 # only the remaining task ran this session


def test_rollup_marks_story_done(tmp_path):
    epic = _epic()
    runner.run_epic(tmp_path, epic, lambda t: (True, "ok"))
    state = runner.load_state(tmp_path, epic)
    assert runner.status_of(state, "E-0001.S1") == runner.DONE
    assert runner.status_of(state, "E-0001") == runner.DONE


def test_exception_in_task_marks_failed_not_crash(tmp_path):
    epic = _epic()

    def boom(task):
        raise RuntimeError("kaboom")

    summary = runner.run_epic(tmp_path, epic, boom)
    assert summary["failed"] == 1  # T1 failed; graph did not crash
