from devagent.context.index import build_index
from devagent.context.retrieve import retrieve
from devagent.decompose.planner import _extract_json_array, decompose, should_decompose


def test_extract_json_array_fenced():
    text = 'here\n```json\n[{"id":"s1","description":"do x"}]\n```\nend'
    arr = _extract_json_array(text)
    assert arr and arr[0]["id"] == "s1"


def test_extract_json_array_bare():
    arr = _extract_json_array('[{"id":"s1","description":"y"}]')
    assert arr and arr[0]["description"] == "y"


def test_extract_json_array_garbage():
    assert _extract_json_array("no json here") is None


def test_should_decompose_small_is_false(tmp_repo):
    idx = build_index(tmp_repo)
    b = retrieve(idx, "fix the add method", max_context_tokens=12000, max_file_lines=400)
    assert should_decompose(b, max_subtask_files=3) is False


def test_decompose_direct_makes_no_model_call(tmp_repo):
    """A small in-envelope task must NOT consult the planner (router stays untouched)."""
    idx = build_index(tmp_repo)
    b = retrieve(idx, "fix the add method", max_context_tokens=12000, max_file_lines=400)

    class BoomRouter:
        last_model = None
        last_tier = None

        def complete(self, *a, **k):
            raise AssertionError("planner must not be called for an in-envelope task")

    plan = decompose("fix add", idx, b, BoomRouter(), max_subtask_files=3)
    assert plan.decomposed is False
    assert len(plan.subtasks) == 1


def test_decompose_force_direct(tmp_repo):
    idx = build_index(tmp_repo)
    b = retrieve(idx, "anything", max_context_tokens=12000, max_file_lines=400)
    plan = decompose("t", idx, b, None, max_subtask_files=3, force_direct=True)
    assert plan.decomposed is False and len(plan.subtasks) == 1
