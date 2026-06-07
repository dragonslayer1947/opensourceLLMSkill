"""Repo-index cache (V2). Serializes the AST index to `.devagent/cache/index.json`, keyed on a
fingerprint of every source file's (rel, mtime, size). On the next run the fingerprint is
recomputed (cheap stat walk) and the cache is reused if unchanged — skipping the ast-parse cost
on large repos. Any add/remove/edit invalidates it automatically."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .index import FileEntry, RepoIndex, Symbol, build_index, source_paths

CACHE_FILE = ".devagent/cache/index.json"


def fingerprint(root: Path) -> str:
    h = hashlib.sha1()
    for path, rel in source_paths(root):
        try:
            st = path.stat()
        except OSError:
            continue
        h.update(f"{rel}:{st.st_mtime_ns}:{st.st_size}\n".encode("utf-8"))
    return h.hexdigest()


def _index_to_dict(index: RepoIndex) -> dict:
    return {"files": [{
        "rel": f.rel, "lines": f.lines,
        "symbols": [vars(s) for s in f.symbols],
        "imports": f.imports, "terms": sorted(f.terms),
        "routes_defined": sorted(f.routes_defined), "routes_used": sorted(f.routes_used),
        "topics": sorted(f.topics), "vector": f.vector,
    } for f in index.files]}


def _index_from_dict(root: Path, data: dict) -> RepoIndex:
    index = RepoIndex(root=root)
    for fd in data.get("files", []):
        index.files.append(FileEntry(
            path=root / fd["rel"], rel=fd["rel"], lines=fd.get("lines", 0),
            symbols=[Symbol(**s) for s in fd.get("symbols", [])],
            imports=list(fd.get("imports", [])), terms=set(fd.get("terms", [])),
            routes_defined=set(fd.get("routes_defined", [])),
            routes_used=set(fd.get("routes_used", [])),
            topics=set(fd.get("topics", [])), vector=fd.get("vector"),
        ))
    return index


def build_index_cached(root: str | Path, *, use_cache: bool = True, embedder=None) -> RepoIndex:
    root = Path(root).resolve()
    cache_path = root / CACHE_FILE
    fp = fingerprint(root)

    cached: RepoIndex | None = None
    if use_cache and cache_path.exists():
        try:
            data = json.loads(cache_path.read_text(encoding="utf-8"))
            if data.get("fingerprint") == fp:
                cached = _index_from_dict(root, data)
        except (OSError, json.JSONDecodeError, TypeError):
            pass  # corrupt/old cache -> rebuild

    index = cached if cached is not None else build_index(root)

    # Optional semantic tier (gap #4): compute embeddings for files that lack a cached vector,
    # then persist so the next run reuses them. No-op when no embedder is configured.
    if embedder is not None:
        from .embed import attach_embeddings
        if attach_embeddings(index, embedder) and use_cache:
            cached = None  # vectors changed -> rewrite cache below

    if use_cache and cached is None:
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"fingerprint": fp, **_index_to_dict(index)}
            cache_path.write_text(json.dumps(payload), encoding="utf-8")
        except OSError:
            pass  # caching is best-effort
    return index
