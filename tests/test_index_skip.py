"""Regression: a non-standard venv name (`.venv-asr`) and any `site-packages` must be ignored.
Reported live — a repo with a `.venv-asr/` indexed 2590 files and produced a 549-file blast
radius made entirely of dependency code."""
from devagent.context.index import build_index, source_paths


def _write(root, rel, text="x = 1\n"):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def test_nonstandard_venv_and_site_packages_excluded(tmp_path):
    _write(tmp_path, "src/app.py", "def main():\n    return 1\n")
    _write(tmp_path, "README.md")  # not a source suffix anyway
    # the bug: a venv named .venv-asr with site-packages
    _write(tmp_path, ".venv-asr/Lib/site-packages/anyio/__init__.py")
    _write(tmp_path, ".venv-asr/Lib/site-packages/foo/bar.py")
    _write(tmp_path, "node_modules/pkg/index.js")
    _write(tmp_path, "pkg/__pycache__/cached.py")
    _write(tmp_path, "thing.egg-info/top.py")

    rels = {rel for _, rel in source_paths(tmp_path)}
    assert rels == {"src/app.py"}


def test_pyvenv_cfg_marks_a_venv_even_with_an_unknown_name(tmp_path):
    _write(tmp_path, "src/app.py")
    # a venv with a name no pattern would catch, identified only by pyvenv.cfg
    _write(tmp_path, "weirdenvname/pyvenv.cfg", "home = /usr\n")
    _write(tmp_path, "weirdenvname/src/leak.py")  # would leak in without pyvenv.cfg pruning

    rels = {rel for _, rel in source_paths(tmp_path)}
    assert "src/app.py" in rels
    assert "weirdenvname/src/leak.py" not in rels


def test_build_index_only_real_files(tmp_path):
    _write(tmp_path, "a.py", "def f():\n    return 1\n")
    _write(tmp_path, ".venv/Lib/site-packages/dep.py")
    idx = build_index(tmp_path)
    assert [f.rel for f in idx.files] == ["a.py"]
