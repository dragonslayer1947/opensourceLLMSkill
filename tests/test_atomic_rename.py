"""Atomic cross-cutting rename (Tier-1 #3): identifier-accurate, all-or-nothing."""
from devagent.execute import atomic_rename


def _w(root, rel, text):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def test_rewrite_python_skips_strings_comments_and_substrings():
    text = "foo = 1\ns = 'foo'  # foo here\nfoobar = foo\n"
    out, n = atomic_rename.rewrite(text, "foo", "baz", is_python=True)
    assert "baz = 1" in out
    assert "'foo'" in out          # string literal untouched
    assert "# foo here" in out     # comment untouched
    assert "foobar = baz" in out   # `foobar` (substring) untouched; the bare `foo` renamed
    assert n == 2


def test_atomic_rename_coordinates_across_files(tmp_path, make_config):
    _w(tmp_path, "a.py", "def helper():\n    return 1\n")
    _w(tmp_path, "b.py", "from a import helper\n\nx = helper()\n")
    snap = tmp_path / ".devagent" / "snap"
    res = atomic_rename.apply_rename(tmp_path, "helper", "do_thing", make_config().gate, snap)
    assert res.ok and len(res.changed) == 2
    assert "def do_thing" in (tmp_path / "a.py").read_text()
    b = (tmp_path / "b.py").read_text()
    assert "from a import do_thing" in b and "helper" not in b  # importer kept consistent


def test_atomic_rename_rolls_back_all_on_gate_failure(tmp_path, make_config):
    original = "foo = 1\nprint(foo)\n"
    _w(tmp_path, "a.py", original)
    # renaming to a keyword breaks syntax -> gate fails -> the whole transaction reverts
    res = atomic_rename.apply_rename(tmp_path, "foo", "class", make_config().gate,
                                     tmp_path / ".devagent" / "snap")
    assert not res.ok and res.rolled_back
    assert (tmp_path / "a.py").read_text() == original   # restored byte-for-byte


def test_no_occurrences_is_a_noop(tmp_path, make_config):
    _w(tmp_path, "a.py", "x = 1\n")
    res = atomic_rename.apply_rename(tmp_path, "absent", "renamed", make_config().gate,
                                     tmp_path / ".devagent" / "snap")
    assert res.ok and res.occurrences == 0 and res.changed == []
