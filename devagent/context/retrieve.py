"""Precise retrieval — attacks the repo-scale half of the parity problem.

Score files/symbols against the task by keyword overlap (free, local, deterministic), then
assemble a context bundle capped at the envelope's token budget. Large files are windowed.
No model call, no repo dump — the executor sees only the relevant slice."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .index import RepoIndex
from .window import FileView, view_file

_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_STOP = {"the", "a", "an", "to", "of", "in", "and", "or", "add", "fix", "update", "make",
         "for", "with", "on", "this", "that", "it", "is", "be", "function", "file", "code"}


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


def _task_terms(task: str) -> set[str]:
    return {w.lower() for w in _WORD.findall(task) if w.lower() not in _STOP and len(w) > 2}


def _score_file(entry, terms: set[str], explicit_paths: set[str]) -> float:
    score = 0.0
    rel_l = entry.rel.lower()
    if entry.rel in explicit_paths or Path(entry.rel).name in explicit_paths:
        score += 100.0
    for t in terms:
        if t in rel_l:
            score += 3.0
    for sym in getattr(entry, "symbols", []):
        name_l = sym.name.lower()
        for t in terms:
            if t == name_l or t == name_l.split(".")[-1]:
                score += 5.0
            elif t in name_l:
                score += 1.5
    # content-term overlap: matches tasks that reference code in file bodies, not just names
    body = getattr(entry, "terms", set())
    if body:
        score += 2.0 * len(terms & body)
    return score


def retrieve(
    index: RepoIndex,
    task: str,
    *,
    max_context_tokens: int,
    max_file_lines: int,
    max_files: int = 4,
    explicit_paths: set[str] | None = None,
) -> ContextBundle:
    terms = _task_terms(task)
    explicit = explicit_paths or set()
    scored = [(e, _score_file(e, terms, explicit)) for e in index.files]
    scored = [(e, s) for e, s in scored if s > 0]
    scored.sort(key=lambda x: x[1], reverse=True)

    bundle = ContextBundle(candidate_files=[e.rel for e, _ in scored[:10]])
    budget = max_context_tokens
    # Pick a focus symbol per file from the highest-scoring matching symbol.
    for entry, _ in scored[:max_files]:
        focus_symbol = None
        for sym in getattr(entry, "symbols", []):
            if any(t in sym.name.lower() for t in terms):
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
