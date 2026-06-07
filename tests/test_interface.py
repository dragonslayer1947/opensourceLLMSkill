"""Cross-file interface resolution (gap #2): catch a name one subtask imports that another
subtask never defined."""
from devagent.validate import interface


def _w(root, rel, text):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def test_flags_dangling_import(tmp_path):
    _w(tmp_path, "app/store.py", "def list_services():\n    return []\n")
    # imports a name the target module does NOT define (drift)
    _w(tmp_path, "app/api.py", "from app.store import get_service\n\ndef h():\n    return get_service()\n")
    issues = interface.check_imports(tmp_path)
    assert any("get_service" in i and "app/api.py" in i for i in issues)


def test_clean_imports_have_no_issues(tmp_path):
    _w(tmp_path, "app/store.py", "def list_services():\n    return []\n")
    _w(tmp_path, "app/api.py", "from app.store import list_services\n\ndef h():\n    return list_services()\n")
    assert interface.check_imports(tmp_path) == []


def test_module_level_singleton_resolves(tmp_path):
    # `store = X()` is a top-level binding; importing it must NOT be flagged
    _w(tmp_path, "app/store.py", "class S:\n    pass\n\nstore = S()\n")
    _w(tmp_path, "app/main.py", "from app.store import store\n\nx = store\n")
    assert interface.check_imports(tmp_path) == []


def test_submodule_import_is_valid(tmp_path):
    _w(tmp_path, "pkg/sub.py", "VALUE = 1\n")
    _w(tmp_path, "pkg/top.py", "from pkg import sub\n\ny = sub.VALUE\n")
    assert interface.check_imports(tmp_path) == []


def test_stdlib_and_relative_imports_ignored(tmp_path):
    _w(tmp_path, "app/x.py", "import os\nfrom typing import List\n\nv: List = []\n")
    assert interface.check_imports(tmp_path) == []


def test_top_level_names_covers_defs_classes_assigns():
    import ast
    tree = ast.parse("import os\nX = 1\ndef f():\n    pass\nclass C:\n    pass\nY: int = 2\n")
    names = interface.top_level_names(tree)
    assert {"os", "X", "f", "C", "Y"} <= names
