"""Multi-language continuity, phase 1 (gap #4): JS/TS files get symbol extraction and a resolved
import graph, so blast radius / retrieval span them — not just Python. (Tree-sitter-grade parsing
is a later phase; see docs/MULTI_LANGUAGE.md.)"""
from devagent.context.index import _resolve_js_import, build_index
from devagent.planning import blast_radius


def _w(root, rel, text):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def test_js_imports_resolve_and_extend_blast_radius(tmp_path):
    _w(tmp_path, "src/util.js", "export function add(a, b) { return a + b }\n")
    _w(tmp_path, "src/app.js", "import { add } from './util'\nconsole.log(add(1, 2))\n")
    idx = build_index(tmp_path)
    by = {f.rel: f for f in idx.files}
    assert by["src/app.js"].lang == "js"
    assert "src/util.js" in by["src/app.js"].import_targets

    # The import graph (and thus blast radius) now spans JS, with no Python involved.
    deps = blast_radius.build_dependents(idx)
    assert "src/app.js" in deps["src/util.js"]
    assert "src/app.js" in blast_radius.analyze(idx, ["src/util.js"]).affected


def test_js_symbols_extracted(tmp_path):
    _w(tmp_path, "a.ts", "export class Foo {}\nexport const bar = 1\n"
                         "export function baz() {}\nexport { qux as quux }\n")
    idx = build_index(tmp_path)
    names = {s.name for s in idx.files[0].symbols}
    assert {"Foo", "bar", "baz", "quux"} <= names


def test_require_and_relative_parent_imports(tmp_path):
    _w(tmp_path, "lib/m.js", "function f() {}\nmodule.exports = { f }\n")
    _w(tmp_path, "lib/use.js", "const { f } = require('./m')\n")
    _w(tmp_path, "x.js", "import lib from './lib/use'\n")
    idx = build_index(tmp_path)
    by = {f.rel: f for f in idx.files}
    assert "lib/m.js" in by["lib/use.js"].import_targets
    assert "lib/use.js" in by["x.js"].import_targets


def test_resolve_js_import_rules():
    rels = {"src/util.js", "src/sub/index.ts", "src/a.tsx"}
    assert _resolve_js_import("src/app.js", "./util", rels) == "src/util.js"
    assert _resolve_js_import("src/app.js", "./sub", rels) == "src/sub/index.ts"   # dir -> index
    assert _resolve_js_import("src/deep/x.js", "../a", rels) == "src/a.tsx"        # parent + ext
    assert _resolve_js_import("src/app.js", "react", rels) is None                 # external pkg
    assert _resolve_js_import("src/app.js", "./missing", rels) is None


def test_python_path_unaffected(tmp_path):
    # Python continuity must be unchanged by the multi-language seam.
    _w(tmp_path, "pkg/a.py", "def helper():\n    return 1\n")
    _w(tmp_path, "pkg/b.py", "from pkg.a import helper\n\nx = helper()\n")
    idx = build_index(tmp_path)
    by = {f.rel: f for f in idx.files}
    assert by["pkg/a.py"].lang == "py"
    assert "pkg/b.py" in blast_radius.build_dependents(idx)["pkg/a.py"]
