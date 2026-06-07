"""Retrieval eval (gap #5) — measure whether retrieval actually finds the right file.

The whole parity argument assumes the executor is handed the RIGHT slice. On a big repo that's an
unproven assumption. This gives a label-free, reproducible metric: for each source file, build a
query from its own identity (path words + top symbol names) and check that retrieval ranks that
file into the top-k. It's *self-retrieval* recall — a health/regression signal for the ranker, not
a substitute for task-level eval, but it catches the failure mode "the right file isn't even
retrievable." Pure Python, deterministic, no model calls."""
from __future__ import annotations

from dataclasses import dataclass

from ..context import rag
from ..planning.blast_radius import build_dependents


@dataclass
class RetrievalEval:
    n: int
    recall_at_k: float
    mrr: float
    k: int
    misses: list[str]

    def render(self) -> str:
        return (f"retrieval self-recall@{self.k}: {self.recall_at_k:.0%}  "
                f"(MRR {self.mrr:.2f}, {self.n} files)")


def _query_for(entry) -> str:
    words = entry.rel.replace("/", " ").replace("_", " ").rsplit(".", 1)[0]
    syms = " ".join(s.name.split(".")[-1] for s in entry.symbols[:4])
    return f"{words} {syms}".strip()


def evaluate(index, k: int = 5, file_vectors=None) -> RetrievalEval:
    """Self-retrieval recall@k + MRR over every file that has symbols (so the query is meaningful)."""
    dependents = build_dependents(index) if index.files else {}
    cases = [f for f in index.files if f.symbols]
    if not cases:
        return RetrievalEval(0, 1.0, 1.0, k, [])
    hits = 0
    rr_sum = 0.0
    misses: list[str] = []
    for f in cases:
        ranked = rag.rank_files(index, _query_for(f), dependents=dependents, limit=k)
        if f.rel in ranked:
            hits += 1
            rr_sum += 1.0 / (ranked.index(f.rel) + 1)
        else:
            misses.append(f.rel)
    n = len(cases)
    return RetrievalEval(n=n, recall_at_k=hits / n, mrr=rr_sum / n, k=k, misses=misses[:20])
