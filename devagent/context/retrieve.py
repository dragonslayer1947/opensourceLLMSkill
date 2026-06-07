"""Precise retrieval — attacks the repo-scale half of the parity problem.

Score files/symbols against the task by keyword overlap (free, local, deterministic), then
assemble a context bundle capped at the envelope's token budget. Large files are windowed.
No model call, no repo dump — the executor sees only the relevant slice."""
from __future__ import annotations

from dataclasses import dataclass, field

from . import rag
from .index import RepoIndex
from .window import FileView, view_file


@dataclass
class ContextBundle:
    views: list[FileView] = field(default_factory=list)
    est_tokens: int = 0
    in_envelope: bool = True
    candidate_files: list[str] = field(default_factory=list)  # rel paths, ranked

    def render(self) -> str:
        parts = []
        for v in self.views:
            tag = " (windowed)" if v.windowed else ""
            parts.append(f"=== {v.rel}{tag} ===\n{v.content}")
        return "\n\n".join(parts)


def _tokens(text: str) -> int:
    return max(1, len(text) // 4)


def retrieve(
    index: RepoIndex,
    task: str,
    *,
    max_context_tokens: int,
    max_file_lines: int,
    max_files: int = 4,
    explicit_paths: set[str] | None = None,
) -> ContextBundle:
    from ..planning.blast_radius import build_dependents
    explicit = explicit_paths or set()
    by_rel = {e.rel: e for e in index.files}

    # Three-tier ranking (exact + BM25 + graph). Explicit paths are forced to the front.
    dependents = build_dependents(index) if index.files else {}
    ranked = rag.rank_files(index, task, dependents=dependents, limit=10)
    explicit_rels = [e.rel for e in index.files
                     if e.rel in explicit or e.rel.rsplit("/", 1)[-1] in explicit]
    ordered = explicit_rels + [r for r in ranked if r not in explicit_rels]

    bundle = ContextBundle(candidate_files=ordered[:10])
    if not ordered:
        return bundle

    budget = max_context_tokens
    qterms = set(rag.tokenize(task))
    for rel in ordered[:max_files]:
        entry = by_rel.get(rel)
        if entry is None:
            continue
        focus_symbol = None
        for sym in getattr(entry, "symbols", []):
            if any(t in sym.name.lower() for t in qterms):
                focus_symbol = sym.name
                break
        view = view_file(entry.path, entry.rel, max_file_lines=max_file_lines, focus_symbol=focus_symbol)
        cost = _tokens(view.content)
        if bundle.est_tokens + cost > budget and bundle.views:
            break
        bundle.views.append(view)
        bundle.est_tokens += cost

    bundle.in_envelope = bundle.est_tokens <= max_context_tokens
    return bundle
