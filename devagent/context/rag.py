"""Three-tier retrieval ranking (V4).

  Tier 1 — exact: query terms that equal a symbol name or file stem (highest weight).
  Tier 2 — lexical: BM25 over each file's path + symbols + content terms (a dependency-free
           proxy for semantic search; a vector/embedding tier can slot in here later).
  Tier 3 — graph: pull in the dependents/dependencies of top hits via the import graph.

Pure Python, no embeddings or external services — so it runs offline and is deterministic."""
from __future__ import annotations

import math
import re

_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_STOP = {"the", "a", "an", "to", "of", "in", "and", "or", "for", "with", "on", "this", "that",
         "it", "is", "be", "add", "make", "all", "into", "from", "fix", "update"}


def tokenize(text: str) -> list[str]:
    out = []
    for w in _WORD.findall(text or ""):
        wl = w.lower()
        if len(wl) > 2 and wl not in _STOP:
            out.append(wl)
        # split snake/camel so "list_products" also yields "list","products"
        for part in re.split(r"[_]|(?<=[a-z])(?=[A-Z])", w):
            pl = part.lower()
            if len(pl) > 2 and pl not in _STOP and pl != wl:
                out.append(pl)
    return out


def _file_tokens(entry) -> list[str]:
    toks = tokenize(entry.rel.replace("/", " ").replace(".", " "))
    for s in getattr(entry, "symbols", []):
        toks += tokenize(s.name)
    toks += list(getattr(entry, "terms", set()))
    return toks


def _bm25(corpus: dict[str, list[str]], query: list[str], k1: float = 1.5, b: float = 0.75):
    n = len(corpus) or 1
    avgdl = (sum(len(t) for t in corpus.values()) / n) or 1.0
    df: dict[str, int] = {}
    for toks in corpus.values():
        for term in set(toks):
            df[term] = df.get(term, 0) + 1
    scores: dict[str, float] = {}
    for doc_id, toks in corpus.items():
        dl = len(toks) or 1
        tf: dict[str, int] = {}
        for t in toks:
            tf[t] = tf.get(t, 0) + 1
        s = 0.0
        for q in query:
            if q not in tf:
                continue
            idf = math.log((n - df.get(q, 0) + 0.5) / (df.get(q, 0) + 0.5) + 1)
            f = tf[q]
            s += idf * (f * (k1 + 1)) / (f + k1 * (1 - b + b * dl / avgdl))
        if s > 0:
            scores[doc_id] = s
    return scores


def rank_files(index, query: str, *, dependents: dict[str, set[str]] | None = None,
               limit: int = 10) -> list[str]:
    """Return repo-relative file paths ranked for the query (tiers 1–3 combined)."""
    q = tokenize(query)
    if not q:
        return []
    corpus = {f.rel: _file_tokens(f) for f in index.files}
    scores = _bm25(corpus, q)

    # Tier 1 — exact symbol/stem matches get a strong boost.
    qset = set(q)
    for f in index.files:
        stem = f.rel.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()
        names = {s.name.lower().split(".")[-1] for s in getattr(f, "symbols", [])}
        if stem in qset or (names & qset):
            scores[f.rel] = scores.get(f.rel, 0.0) + 5.0

    # Tier 3 — graph expansion: add neighbors of the top hits at a discount.
    if dependents:
        deps_of = dependents
        consumers = {}  # file -> files it imports (reverse of dependents)
        for owner, dset in dependents.items():
            for d in dset:
                consumers.setdefault(d, set()).add(owner)
        top = sorted(scores, key=lambda k: scores[k], reverse=True)[:3]
        for t in top:
            for nb in (deps_of.get(t, set()) | consumers.get(t, set())):
                if nb not in scores:
                    scores[nb] = 0.3 * scores[t]

    return [rel for rel, _ in sorted(scores.items(), key=lambda x: x[1], reverse=True)][:limit]
