"""Impact-scoped verification (gap #1): the right tests get selected for a change."""
from devagent.context.index import build_index
from devagent.validate import impact


def _make_repo(tmp_path):
    pkg = tmp_path / "app"
    pkg.mkdir()
    (pkg / "scheduling.py").write_text("def available_slots():\n    return []\n", encoding="utf-8")
    # providers_api imports scheduling -> a dependent of scheduling
    (pkg / "providers_api.py").write_text(
        "from app.scheduling import available_slots\n\ndef slots():\n    return available_slots()\n",
        encoding="utf-8")
    (pkg / "unrelated.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    tests = tmp_path / "tests"
    tests.mkdir()
    # imports the dependent (providers_api) -> should be selected for a scheduling change
    (tests / "test_api.py").write_text(
        "from app.providers_api import slots\n\ndef test_slots():\n    assert slots() == []\n",
        encoding="utf-8")
    # imports scheduling directly -> should be selected
    (tests / "test_sched.py").write_text(
        "from app.scheduling import available_slots\n\ndef test_s():\n    assert available_slots() == []\n",
        encoding="utf-8")
    # imports something unrelated -> should NOT be selected
    (tests / "test_other.py").write_text(
        "from app.unrelated import foo\n\ndef test_o():\n    assert foo() == 1\n", encoding="utf-8")
    return tmp_path


def test_selects_tests_covering_the_blast_radius(tmp_path):
    repo = _make_repo(tmp_path)
    idx = build_index(repo)
    selected = impact.select_impacted_tests(idx, ["app/scheduling.py"])
    assert "tests/test_sched.py" in selected      # imports the changed module directly
    assert "tests/test_api.py" in selected        # imports a transitive dependent
    assert "tests/test_other.py" not in selected  # unrelated → excluded


def test_changed_test_file_is_selected(tmp_path):
    repo = _make_repo(tmp_path)
    idx = build_index(repo)
    selected = impact.select_impacted_tests(idx, ["tests/test_other.py"])
    assert selected == ["tests/test_other.py"]


def test_verify_skips_when_no_runner(tmp_path):
    repo = _make_repo(tmp_path)
    idx = build_index(repo)
    # a bogus runner that isn't installed → skipped, treated as passing
    res = impact.verify_impact(repo, ["app/scheduling.py"], idx, {"test_command": "no_such_runner_xyz"})
    assert res.passed and res.scope == "skipped"


def test_impacted_modules_includes_dependents(tmp_path):
    repo = _make_repo(tmp_path)
    idx = build_index(repo)
    files, mods = impact.impacted_modules(idx, ["app/scheduling.py"])
    assert "app/providers_api.py" in files        # dependent pulled in
    assert "app.scheduling" in mods
