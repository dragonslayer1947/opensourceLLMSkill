from devagent.context.index import build_index
from devagent.planning.blast_radius import analyze, build_dependents


def _make_repo(tmp_path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "core.py").write_text("def base():\n    return 1\n", encoding="utf-8")
    (pkg / "mid.py").write_text("from pkg.core import base\n\ndef m():\n    return base()\n", encoding="utf-8")
    (pkg / "top.py").write_text("from pkg.mid import m\n\ndef t():\n    return m()\n", encoding="utf-8")
    (pkg / "lonely.py").write_text("x = 1\n", encoding="utf-8")
    return tmp_path


def test_dependents_mapping(tmp_path):
    repo = _make_repo(tmp_path)
    idx = build_index(repo)
    deps = build_dependents(idx)
    # mid imports core -> core has mid as a dependent
    assert "pkg/mid.py" in deps["pkg/core.py"]
    assert "pkg/top.py" in deps["pkg/mid.py"]


def test_transitive_blast_radius(tmp_path):
    repo = _make_repo(tmp_path)
    idx = build_index(repo)
    br = analyze(idx, ["pkg/core.py"], warn=10, block=40)
    # changing core reaches mid (direct) and top (transitive)
    assert set(br.affected) == {"pkg/mid.py", "pkg/top.py"}
    assert br.score == 2 and br.level == "low"


def test_leaf_change_has_no_dependents(tmp_path):
    repo = _make_repo(tmp_path)
    idx = build_index(repo)
    br = analyze(idx, ["pkg/top.py"], warn=10, block=40)
    assert br.affected == [] and br.score == 0


def test_levels(tmp_path):
    repo = _make_repo(tmp_path)
    idx = build_index(repo)
    br = analyze(idx, ["pkg/core.py"], warn=2, block=3)
    assert br.level == "medium"  # score 2 >= warn 2, < block 3
