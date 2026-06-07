from pathlib import Path

from devagent.execute.edits import apply_edit, parse_edits


def test_parse_single_block():
    text = (
        "app/x.py\n"
        "<<<<<<< SEARCH\n"
        "old line\n"
        "=======\n"
        "new line\n"
        ">>>>>>> REPLACE\n"
    )
    edits = parse_edits(text)
    assert len(edits) == 1
    assert edits[0].path == "app/x.py"
    assert edits[0].search.strip() == "old line"
    assert edits[0].replace.strip() == "new line"


def test_parse_multiple_blocks():
    text = (
        "a.py\n<<<<<<< SEARCH\nx\n=======\ny\n>>>>>>> REPLACE\n\n"
        "b.py\n<<<<<<< SEARCH\nm\n=======\nn\n>>>>>>> REPLACE\n"
    )
    assert len(parse_edits(text)) == 2


def test_apply_exact(tmp_path):
    f = tmp_path / "f.py"
    f.write_text("def a():\n    return 1\n", encoding="utf-8")
    edit = parse_edits("f.py\n<<<<<<< SEARCH\n    return 1\n=======\n    return 2\n>>>>>>> REPLACE\n")[0]
    result, old, new = apply_edit(tmp_path, edit)
    assert result.ok and result.reason == "exact"
    assert "return 2" in new and "return 1" in old


def test_apply_create(tmp_path):
    edit = parse_edits("new.py\n<<<<<<< SEARCH\n=======\nprint('hi')\n>>>>>>> REPLACE\n")[0]
    result, old, new = apply_edit(tmp_path, edit)
    assert result.ok and old is None and "hi" in new


def test_apply_normalized_match(tmp_path):
    f = tmp_path / "f.py"
    # trailing whitespace on a line breaks an exact substring match but not a normalized one
    f.write_text("def a():\n    x = 1   \n    return x\n", encoding="utf-8")
    edit = parse_edits(
        "f.py\n<<<<<<< SEARCH\n    x = 1\n    return x\n=======\n    return 2\n>>>>>>> REPLACE\n")[0]
    result, old, new = apply_edit(tmp_path, edit)
    assert result.ok and result.reason == "normalized"
    assert "return 2" in new


def test_apply_path_escape_blocked(tmp_path):
    edit = parse_edits("../evil.py\n<<<<<<< SEARCH\n=======\nx\n>>>>>>> REPLACE\n")[0]
    result, old, new = apply_edit(tmp_path, edit)
    assert not result.ok and "escape" in result.reason


def test_apply_missing_file(tmp_path):
    edit = parse_edits("nope.py\n<<<<<<< SEARCH\nx\n=======\ny\n>>>>>>> REPLACE\n")[0]
    result, old, new = apply_edit(tmp_path, edit)
    assert not result.ok


def test_resolve_placeholder_path_by_basename(tmp_path):
    """A local model that emits the prompt's `path/to/` placeholder should still resolve to the
    real file by basename, and a whitespace-sloppy SEARCH should still match."""
    from devagent.execute.edits import Edit, apply_edit
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    edit = Edit(
        path="path/to/calc.py",
        search="def add(a, b):\n    return a + b\n\n\n",   # extra blank lines (sloppy)
        replace="def add(a, b):\n    return a + b\n\n\ndef multiply(a, b):\n    return a * b\n",
    )
    result, old, new = apply_edit(tmp_path, edit)
    assert result.ok and result.path == "calc.py"
    assert "def multiply(a, b)" in new


def test_resolve_keeps_path_for_new_file(tmp_path):
    from devagent.execute.edits import Edit, apply_edit
    edit = Edit(path="newmod.py", search="", replace="X = 1\n")
    result, old, new = apply_edit(tmp_path, edit)
    assert result.ok and result.path == "newmod.py" and old is None
