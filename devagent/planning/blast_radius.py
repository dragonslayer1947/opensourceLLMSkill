"""Blast radius — intra-service impact analysis from the AST import graph.

Given the files a task will touch, find which other files (transitively) import them — the
change's downstream reach. Reported before execution so the impact is visible up front; a high
score prompts confirmation. V1.5 is intra-repo (Python imports); cross-service reach (events,
HTTP, shared DB) is V2 (gap #3)."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from ..context.index import RepoIndex


@dataclass
class BlastRadius:
    changed: list[str]
    affected: list[str] = field(default_factory=list)
    score: int = 0
    level: str = "low"  # low | medium | high

    def render(self) -> str:
        head = f"blast radius: {self.score} file(s) affected — {self.level}"
        if not self.affected:
            return head + " (no known dependents)"
        shown = ", ".join(self.affected[:8]) + ("…" if len(self.affected) > 8 else "")
        return f"{head}\n  dependents: {shown}"


def _module_keys(rel: str) -> list[str]:
    """Plausible import names for a file path (best-effort, no full import resolution)."""
    noext = rel[:-3] if rel.endswith(".py") else rel
    dotted = noext.replace("/", ".")
    parts = dotted.split(".")
    keys = {dotted, parts[-1]}
    if parts[-1] == "__init__" and len(parts) > 1:
        keys.add(".".join(parts[:-1]))
        keys.add(parts[-2])
    return [k for k in keys if k]


def build_dependents(index: RepoIndex) -> dict[str, set[str]]:
    """Map each file -> the set of files that import it."""
    key_to_file: dict[str, str] = {}
    for f in index.files:
        for k in _module_keys(f.rel):
            key_to_file.setdefault(k, f.rel)

    dependents: dict[str, set[str]] = {f.rel: set() for f in index.files}
    for f in index.files:
        for imp in getattr(f, "imports", []):
            if not imp:
                continue
            target = key_to_file.get(imp) or key_to_file.get(imp.split(".")[-1])
            if target and target != f.rel:
                dependents[target].add(f.rel)
        # JS/TS: imports are already resolved to repo files (gap #4) — add those edges directly.
        for target in getattr(f, "import_targets", ()) or ():
            if target in dependents and target != f.rel:
                dependents[target].add(f.rel)

    # Merge cross-service (HTTP/queue) runtime edges — coupling the import graph can't see.
    from . import service_edges
    for src, deps in service_edges.runtime_dependents(index).items():
        dependents.setdefault(src, set()).update(deps)
    return dependents


def analyze(index: RepoIndex, changed: list[str], *, warn: int = 10, block: int = 40,
            max_depth: int = 3) -> BlastRadius:
    dependents = build_dependents(index)
    seen: set[str] = set()
    queue: deque[tuple[str, int]] = deque((c, 0) for c in changed)
    while queue:
        node, depth = queue.popleft()
        if depth >= max_depth:
            continue
        for dep in dependents.get(node, ()):
            if dep not in seen and dep not in changed:
                seen.add(dep)
                queue.append((dep, depth + 1))

    affected = sorted(seen)
    score = len(affected)
    level = "high" if score >= block else "medium" if score >= warn else "low"
    return BlastRadius(changed=list(changed), affected=affected, score=score, level=level)
