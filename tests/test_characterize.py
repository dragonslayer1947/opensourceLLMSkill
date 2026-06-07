"""Characterization-test gate (Tier-1): find untested code and pin its current behavior."""
from devagent.context.index import build_index
from devagent.validate import characterize


def _w(root, rel, text):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def test_find_untested_skips_covered_and_new_files(tmp_path):
    _w(tmp_path, "app/calc.py", "def add(a, b):\n    return a + b\n")          # no test
    _w(tmp_path, "app/util.py", "def slug(s):\n    return s.lower()\n")         # tested below
    _w(tmp_path, "tests/test_util.py", "from app.util import slug\n\ndef test_s():\n    assert slug('A') == 'a'\n")
    idx = build_index(tmp_path)
    untested = characterize.find_untested(idx, ["app/calc.py", "app/util.py", "app/new.py"])
    assert untested == ["app/calc.py"]   # util is covered, new.py doesn't exist yet


def test_pin_keeps_passing_test(tmp_path):
    _w(tmp_path, "app/calc.py", "def add(a, b):\n    return a + b\n")
    code = "from app.calc import add\n\ndef test_add():\n    assert add(1, 2) == 3\n"
    res = characterize.pin(tmp_path, "app/calc.py",
                           generate=lambda rel, src: code,
                           run_test=lambda path: (True, "1 passed"))
    assert res.pinned is True
    assert (tmp_path / res.test_path).exists()           # kept: it pins real behavior
    assert res.test_path == "tests/test_calc_characterization.py"


def test_pin_discards_test_that_fails_on_current_code(tmp_path):
    _w(tmp_path, "app/calc.py", "def add(a, b):\n    return a + b\n")
    res = characterize.pin(tmp_path, "app/calc.py",
                           generate=lambda rel, src: "def test_bad():\n    assert False\n",
                           run_test=lambda path: (False, "1 failed"))
    assert res.pinned is False
    assert not (tmp_path / res.test_path).exists()        # removed: can't pin -> no red test left


def test_pin_handles_empty_generation(tmp_path):
    _w(tmp_path, "app/calc.py", "def add(a, b):\n    return a + b\n")
    res = characterize.pin(tmp_path, "app/calc.py",
                           generate=lambda rel, src: "",
                           run_test=lambda path: (True, ""))
    assert res.pinned is False and "no test" in res.detail


def test_pin_all_over_untested(tmp_path):
    _w(tmp_path, "app/a.py", "def f():\n    return 1\n")
    _w(tmp_path, "app/b.py", "def g():\n    return 2\n")
    idx = build_index(tmp_path)
    pins = characterize.pin_all(tmp_path, idx, ["app/a.py", "app/b.py"],
                                generate=lambda rel, src: f"# test for {rel}\ndef test_x():\n    assert True\n",
                                run_test=lambda path: (True, ""))
    assert len(pins) == 2 and all(p.pinned for p in pins)
