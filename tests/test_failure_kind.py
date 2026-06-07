"""Failure classification for the retrieval feedback loop (gap #7)."""
from devagent.validate import failure_kind as fk
from devagent.validate import interface


def test_is_context_failure_recognizes_shapes():
    assert fk.is_context_failure("app/x.py:3:5 F821 undefined name `store`")
    assert fk.is_context_failure('Name "store" is not defined')
    assert fk.is_context_failure("ImportError: cannot import name 'store' from 'app.store'")
    assert fk.is_context_failure("app/x.py: imports 'store' from 'app.store', which does not define it")
    assert fk.is_context_failure("module 'app' has no attribute 'store'")


def test_is_context_failure_ignores_logic_failures():
    assert not fk.is_context_failure("AssertionError: expected 3 got 4")
    assert not fk.is_context_failure("SyntaxError: invalid syntax")
    assert not fk.is_context_failure("")


def test_missing_names_extracts_identifiers():
    assert "store" in fk.missing_names("F821 undefined name `store`")
    assert "store" in fk.missing_names('name "store" is not defined')
    assert "get_x" in fk.missing_names("cannot import name 'get_x' from 'app.api'")
    assert "settle" in fk.missing_names("app/a.py: imports 'settle' from 'app.bill', which does not define it")


def _w(root, rel, text):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def test_issues_touching_only_flags_changed_files(tmp_path):
    _w(tmp_path, "app/store.py", "store = 1\n")
    # a PRE-EXISTING dangling import in an unrelated file
    _w(tmp_path, "app/legacy.py", "from app.store import gone\n")
    # the file the current subtask just wrote
    _w(tmp_path, "app/new.py", "from app.store import alsogone\n")

    all_issues = interface.check_imports(tmp_path)
    assert len(all_issues) == 2  # both dangling imports exist in the repo

    # scoped to the changed file: only the new one fails the subtask, not the legacy drift
    scoped = interface.issues_touching(tmp_path, ["app/new.py"])
    assert any("app/new.py" in i for i in scoped)
    assert not any("app/legacy.py" in i for i in scoped)


def test_issues_touching_catches_breaking_a_dependency(tmp_path):
    # main imports `store`; the subtask edited store.py and removed it -> main now breaks
    _w(tmp_path, "app/store.py", "other = 1\n")  # `store` no longer defined
    _w(tmp_path, "app/main.py", "from app.store import store\n")
    scoped = interface.issues_touching(tmp_path, ["app/store.py"])  # only store.py changed
    assert any("app/main.py" in i and "store" in i for i in scoped)  # caught via the changed module
