"""Free, local repo index built with Python's `ast`. Extracts modules, classes, function
signatures, and imports — no model call. This is what makes large-repo scale irrelevant:
the executor never sees the repo, only the slice retrieval selects.

V1 indexes Python. Other languages fall back to filename/keyword matching."""
from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path

SKIP_DIRS = {".git", ".devagent", "__pycache__", ".venv", "venv", "node_modules",
             ".mypy_cache", ".ruff_cache", ".pytest_cache", "dist", "build", ".idea"}


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


def _parse_python(path: Path, rel: str, text: str) -> FileEntry:
    entry = FileEntry(path=path, rel=rel, lines=text.count("\n") + 1)
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return entry  # unparseable file still indexed by name
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
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        rel = path.relative_to(root).as_posix()
        if path.suffix == ".py":
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            index.files.append(_parse_python(path, rel, text))
        elif path.suffix in {".js", ".ts", ".tsx", ".jsx", ".go", ".java", ".rb", ".rs"}:
            # Non-Python: name-only entry (V1 windowing/retrieval still works by filename).
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").count("\n") + 1
            except OSError:
                lines = 0
            index.files.append(FileEntry(path=path, rel=rel, lines=lines))
    return index
