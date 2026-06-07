"""Free, local repo index built with Python's `ast`. Extracts modules, classes, function
signatures, and imports — no model call. This is what makes large-repo scale irrelevant:
the executor never sees the repo, only the slice retrieval selects.

V1 indexes Python. Other languages fall back to filename/keyword matching."""
from __future__ import annotations

import ast
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_MAX_TERMS_PER_FILE = 400  # cap memory: keep retrieval cheap even on huge repos


def _content_terms(text: str) -> set[str]:
    """A bounded set of lowercased identifiers/words in a file, for content-based retrieval.
    Bounded so the index stays small regardless of repo size."""
    terms: set[str] = set()
    for w in _WORD.findall(text):
        if len(w) > 2:
            terms.add(w.lower())
            if len(terms) >= _MAX_TERMS_PER_FILE:
                break
    return terms

SKIP_DIRS = {".git", ".hg", ".svn", ".devagent", "__pycache__", "__pypackages__",
             "node_modules", ".mypy_cache", ".ruff_cache", ".pytest_cache", ".tox", ".nox",
             ".eggs", ".cache", "dist", "build", ".idea", ".vscode", ".next", ".nuxt",
             ".gradle", ".terraform", "vendor", "htmlcov", "site-packages"}

SOURCE_SUFFIXES = {".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".java", ".rb", ".rs"}


def _skip_dir(name: str) -> bool:
    """A directory we must never descend into. Catches the usual junk, and crucially any
    virtualenv regardless of its name — `.venv`, `.venv-asr`, `venv311`, `env` — plus the
    `site-packages` / metadata dirs inside one. (A definitive `pyvenv.cfg` marker is handled
    separately by pruning during the walk.)"""
    low = name.lower()
    if name in SKIP_DIRS:
        return True
    if low.startswith((".venv", "venv")):
        return True
    return low.endswith((".egg-info", ".dist-info"))


def source_paths(root: Path):
    """Yield (path, rel) for every indexable source file (shared by the index and its cache).

    Uses os.walk with in-place pruning so we never even descend into a virtualenv or
    node_modules — essential on real repos (a venv alone can hold 50k+ files)."""
    root = Path(root).resolve()
    results: list[tuple[Path, str]] = []
    for dirpath, dirnames, filenames in os.walk(root):
        # A directory containing pyvenv.cfg IS a virtualenv root — don't descend into it.
        if "pyvenv.cfg" in filenames:
            dirnames[:] = []
            continue
        dirnames[:] = [d for d in dirnames if not _skip_dir(d)]
        base = Path(dirpath)
        for fn in filenames:
            if Path(fn).suffix in SOURCE_SUFFIXES:
                p = base / fn
                results.append((p, p.relative_to(root).as_posix()))
    results.sort(key=lambda t: t[1])
    yield from results


@dataclass
class Symbol:
    name: str
    kind: str          # "function" | "class" | "method"
    signature: str
    lineno: int
    end_lineno: int


@dataclass
class FileEntry:
    path: Path         # absolute
    rel: str           # repo-relative, posix-style
    lines: int
    symbols: list[Symbol] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)
    terms: set[str] = field(default_factory=set)  # bounded content terms for retrieval
    # Cross-service (runtime) signals for blast radius (gap #3) — see _service_signals.
    routes_defined: set[str] = field(default_factory=set)  # route keys this file SERVES
    routes_used: set[str] = field(default_factory=set)     # route keys this file CALLS
    topics: set[str] = field(default_factory=set)          # pub/sub topic names this file touches
    vector: list[float] | None = None                      # optional semantic embedding (gap #4)
    lang: str = "other"                                    # py | js | other
    import_specs: list[str] = field(default_factory=list)  # raw module specifiers (js/ts: './x')
    import_targets: set[str] = field(default_factory=set)  # resolved repo-relative dependency files


@dataclass
class RepoIndex:
    root: Path
    files: list[FileEntry] = field(default_factory=list)

    def all_symbols(self) -> list[tuple[FileEntry, Symbol]]:
        return [(f, s) for f in self.files for s in f.symbols]


def _signature(node: ast.AST) -> str:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        args = [a.arg for a in node.args.args]
        prefix = "async def " if isinstance(node, ast.AsyncFunctionDef) else "def "
        return f"{prefix}{node.name}({', '.join(args)})"
    if isinstance(node, ast.ClassDef):
        bases = [ast.unparse(b) for b in node.bases] if node.bases else []
        return f"class {node.name}" + (f"({', '.join(bases)})" if bases else "")
    return ""


_HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}
_ROUTE_DECO = _HTTP_METHODS | {"route", "websocket", "api_route"}
_TOPIC_CALLS = {"publish", "subscribe", "produce", "consume", "emit", "send_message"}


def _route_segments(s: str) -> set[str]:
    """Static (non-parameter) path segments of a URL or path string — the join keys between a
    route DEFINITION (`@app.get('/services/{id}')`) and a CALL (`client.get('/services/123')`).
    Using the SET of segments (link on any overlap) tolerates router-prefix mounting: a handler
    decorated `/{id}/cancel` under a `/bookings` prefix still matches a caller of
    `/bookings/{id}/cancel` via the shared `cancel` segment."""
    s = (s or "").strip()
    if "://" in s:
        rest = s.split("://", 1)[1]
        s = "/" + rest.split("/", 1)[1] if "/" in rest else "/"
    s = s.split("?")[0].split("#")[0]
    return {seg.lower() for seg in s.split("/")
            if seg and not seg.startswith(("{", ":", "<")) and not seg.isdigit()}


def _const_str(node: ast.AST) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _looks_like_path(s: str) -> bool:
    return s.startswith("/") or "://" in s


def _service_signals(tree: ast.AST) -> tuple[set[str], set[str], set[str]]:
    """Best-effort, deterministic extraction of cross-service edges from the AST:
    routes a file SERVES (decorators), routes it CALLS (http-client calls), and pub/sub
    topics it touches. No execution, no network — just shape-matching."""
    defined: set[str] = set()
    used: set[str] = set()
    topics: set[str] = set()
    deco_calls: set[int] = set()

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for deco in node.decorator_list:
                if (isinstance(deco, ast.Call) and isinstance(deco.func, ast.Attribute)
                        and deco.func.attr.lower() in _ROUTE_DECO and deco.args):
                    deco_calls.add(id(deco))
                    p = _const_str(deco.args[0])
                    if p and _looks_like_path(p):
                        defined |= _route_segments(p)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or id(node) in deco_calls:
            continue
        func = node.func
        if not isinstance(func, ast.Attribute):
            continue
        attr = func.attr.lower()
        if attr in _HTTP_METHODS and node.args:
            p = _const_str(node.args[0])
            if p and _looks_like_path(p):  # a client.get("/x") / requests.post("http://…/x")
                used |= _route_segments(p)
        elif attr in _TOPIC_CALLS and node.args:
            t = _const_str(node.args[0])
            if t:
                topics.add(t.strip().lower())

    return {k for k in defined if k}, {k for k in used if k}, topics


JS_SUFFIXES = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}

# JS/TS heuristic extraction (gap #4, phase 1 — pre-tree-sitter). Regex-based: catches the common
# 90% (ES module imports, requires, exported symbols) deterministically and with no dependency.
# Tree-sitter replaces these with real parse trees in a later phase (see docs/MULTI_LANGUAGE.md).
_JS_IMPORT = re.compile(
    r"""(?:import\s[^'"]*?from\s*['"]([^'"]+)['"])"""      # import x from 'spec'
    r"""|(?:import\s*['"]([^'"]+)['"])"""                   # import 'spec'
    r"""|(?:export\s[^'"]*?from\s*['"]([^'"]+)['"])"""      # export ... from 'spec'
    r"""|(?:require\(\s*['"]([^'"]+)['"]\s*\))"""           # require('spec')
    r"""|(?:import\(\s*['"]([^'"]+)['"]\s*\))""")           # dynamic import('spec')
_JS_DEF = re.compile(
    r"""export\s+(?:default\s+)?(?:async\s+)?(function|class|const|let|var)\s+([A-Za-z_$][\w$]*)""")
_JS_NAMED_EXPORT = re.compile(r"""export\s*\{([^}]*)\}""")


def _parse_js(path: Path, rel: str, text: str) -> FileEntry:
    entry = FileEntry(path=path, rel=rel, lines=text.count("\n") + 1,
                      terms=_content_terms(text), lang="js")
    specs: list[str] = []
    for m in _JS_IMPORT.finditer(text):
        spec = next((g for g in m.groups() if g), None)
        if spec:
            specs.append(spec)
    entry.import_specs = specs
    for m in _JS_DEF.finditer(text):
        kind, name = m.group(1), m.group(2)
        entry.symbols.append(Symbol(
            name=name, kind="class" if kind == "class" else "function",
            signature=f"export {kind} {name}",
            lineno=text.count("\n", 0, m.start()) + 1, end_lineno=0))
    for m in _JS_NAMED_EXPORT.finditer(text):
        for raw in m.group(1).split(","):
            name = raw.split(" as ")[-1].strip()
            if name and name.isidentifier():
                entry.symbols.append(Symbol(name=name, kind="function",
                                            signature=f"export {{ {name} }}", lineno=0, end_lineno=0))
    return entry


def _resolve_js_import(importer_rel: str, spec: str, relset: set[str]) -> str | None:
    """Resolve a relative JS/TS import specifier to a repo file (best-effort, like Node)."""
    if not spec.startswith("."):
        return None  # bare specifier => external package
    base = importer_rel.rsplit("/", 1)[0] if "/" in importer_rel else ""
    parts = (base.split("/") if base else []) + spec.split("/")
    stack: list[str] = []
    for p in parts:
        if p in ("", "."):
            continue
        if p == "..":
            if stack:
                stack.pop()
        else:
            stack.append(p)
    cand = "/".join(stack)
    options = [cand] + [f"{cand}{ext}" for ext in JS_SUFFIXES] \
        + [f"{cand}/index{ext}" for ext in JS_SUFFIXES]
    for o in options:
        if o in relset:
            return o
    return None


def _parse_python(path: Path, rel: str, text: str) -> FileEntry:
    entry = FileEntry(path=path, rel=rel, lines=text.count("\n") + 1, terms=_content_terms(text),
                      lang="py")
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return entry  # unparseable file still indexed by name
    entry.routes_defined, entry.routes_used, entry.topics = _service_signals(tree)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            entry.symbols.append(Symbol(
                name=node.name,
                kind="class" if isinstance(node, ast.ClassDef) else "function",
                signature=_signature(node),
                lineno=node.lineno,
                end_lineno=getattr(node, "end_lineno", node.lineno),
            ))
            if isinstance(node, ast.ClassDef):
                for sub in node.body:
                    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        entry.symbols.append(Symbol(
                            name=f"{node.name}.{sub.name}",
                            kind="method",
                            signature=f"  {_signature(sub)}",
                            lineno=sub.lineno,
                            end_lineno=getattr(sub, "end_lineno", sub.lineno),
                        ))
        elif isinstance(node, ast.Import):
            entry.imports += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            entry.imports.append(node.module or "")
    return entry


def build_index(root: str | Path) -> RepoIndex:
    root = Path(root).resolve()
    index = RepoIndex(root=root)
    for path, rel in source_paths(root):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if path.suffix == ".py":
            index.files.append(_parse_python(path, rel, text))
        elif path.suffix in JS_SUFFIXES:
            index.files.append(_parse_js(path, rel, text))
        else:
            # Unknown language: no symbols, but content terms still enable retrieval.
            index.files.append(FileEntry(
                path=path, rel=rel, lines=text.count("\n") + 1, terms=_content_terms(text)))

    # Resolve JS/TS relative imports to repo files now that every rel path is known (phase 1 of
    # gap #4) — this is what lets the blast radius span JS/TS, not just Python.
    relset = {f.rel for f in index.files}
    for f in index.files:
        if f.lang == "js" and f.import_specs:
            f.import_targets = {t for s in f.import_specs
                                if (t := _resolve_js_import(f.rel, s, relset))}
    return index
