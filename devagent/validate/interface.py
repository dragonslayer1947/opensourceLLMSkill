"""Cross-file interface resolution check (gap #2, detection half).

Independently-built subtasks can disagree on names: subtask A renames a function, subtask B still
imports the old name. The per-file gate never sees it (each file is valid on its own). This walks
the repo's intra-package `from x import y` statements and flags any `y` the target module doesn't
actually define — the classic decomposition drift. Stdlib/third-party imports, `import *`, and
relative imports are skipped; a `from pkg import submodule` (importing a real sibling module) is
treated as valid."""
from __future__ import annotations

import ast
from pathlib import Path

from ..context.index import source_paths


def top_level_names(tree: ast.Module) -> set[str]:
    """Names bound at module top level: defs, classes, assignments, and imported aliases."""
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    names.add(t.id)
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name):
                names.add(node.target.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                names.add((a.asname or a.name).split(".")[0])
    return names


def _dotted(rel: str) -> str:
    return (rel[:-3] if rel.endswith(".py") else rel).replace("/", ".")


def check_imports(root: Path) -> list[str]:
    """Return human-readable issues for intra-repo imports of names that don't exist."""
    root = Path(root)
    files: dict[str, tuple[str, ast.Module, set[str]]] = {}
    for p, rel in source_paths(root):
        if not rel.endswith(".py"):
            continue
        try:
            tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        files[_dotted(rel)] = (rel, tree, top_level_names(tree))

    issues: list[str] = []
    for _dot, (rel, tree, _names) in files.items():
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or node.level:
                continue  # skip relative imports
            mod = node.module
            if not mod or mod not in files:
                continue  # not an intra-repo module file (package / stdlib / third-party)
            target_names = files[mod][2]
            for a in node.names:
                if a.name == "*":
                    continue
                if a.name in target_names:
                    continue
                if f"{mod}.{a.name}" in files:
                    continue  # `from pkg import submodule` — a real sibling module
                issues.append(
                    f"{rel}: imports '{a.name}' from '{mod}', which does not define it")
    return issues
