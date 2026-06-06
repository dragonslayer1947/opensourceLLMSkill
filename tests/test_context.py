from devagent.context.compress import deterministic_extract
from devagent.context.index import build_index
from devagent.context.retrieve import retrieve
from devagent.context.window import view_file


def test_index_symbols_and_terms(tmp_repo):
    idx = build_index(tmp_repo)
    assert len(idx.files) == 1
    f = idx.files[0]
    names = {s.name for s in f.symbols}
    assert "Calc" in names and "Calc.add" in names and "Calc.total" in names
    assert "add" in f.terms or "calc" in f.terms


def test_retrieve_by_symbol_name(tmp_repo):
    idx = build_index(tmp_repo)
    # "total" matches the Calc.total symbol ("add"/"fix" are generic stopwords)
    b = retrieve(idx, "improve the total calculation", max_context_tokens=12000, max_file_lines=400)
    assert b.views and b.views[0].rel == "app/calc.py"
    assert b.in_envelope


def test_retrieve_explicit_path(tmp_repo):
    idx = build_index(tmp_repo)
    b = retrieve(idx, "unrelated wording", max_context_tokens=12000, max_file_lines=400,
                 explicit_paths={"calc.py"})
    assert any(v.rel == "app/calc.py" for v in b.views)


def test_retrieve_empty_when_no_match(tmp_repo):
    idx = build_index(tmp_repo)
    b = retrieve(idx, "zzz nonexistent qqq", max_context_tokens=12000, max_file_lines=400)
    assert b.views == []


def test_window_small_file_passthrough(tmp_repo):
    idx = build_index(tmp_repo)
    f = idx.files[0]
    view = view_file(f.path, f.rel, max_file_lines=400)
    assert not view.windowed and "def add" in view.content


def test_window_large_file_skeleton(tmp_path):
    big = tmp_path / "big.py"
    body = "\n".join(f"def fn_{i}():\n    return {i}" for i in range(100))
    big.write_text(body, encoding="utf-8")
    view = view_file(big, "big.py", max_file_lines=20, focus_symbol="fn_50")
    assert view.windowed
    assert "FILE SKELETON" in view.content and "FOCUS REGION" in view.content
    assert "fn_50" in view.content


def test_deterministic_extract():
    src = "import os\n\nclass MyError(Exception):\n    pass\n\ndef run(x: int) -> int:\n    return x\n"
    out = deterministic_extract("m.py", src)
    assert "import os" in out and "def run" in out and "class MyError" in out
