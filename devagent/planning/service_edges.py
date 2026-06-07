"""Cross-service runtime edges for the blast radius (gap #3).

The import graph (blast_radius.build_dependents) only sees *intra-process* coupling: file A
breaks file B because B imports A. But real systems break across that boundary — a change to
the service that SERVES `GET /services` breaks the client/test that CALLS `/services`, and a
change to a message PRODUCER breaks its CONSUMER, with no import edge between them at all.

This module reconstructs those edges deterministically from signals the index already extracted
(`routes_defined` / `routes_used` / `topics`) — no execution, no network. The edges are merged
into `build_dependents`, so impact-scoped test selection (validate/impact.py) and the up-front
blast-radius report both see HTTP- and queue-coupled reach, not just imports."""
from __future__ import annotations

from collections import defaultdict

from ..context.index import RepoIndex


def runtime_dependents(index: RepoIndex) -> dict[str, set[str]]:
    """Extra `definer -> {dependents}` edges from HTTP routes and pub/sub topics.

    Route edge: a file that CALLS route R depends on every file that SERVES R (change the
    server → the caller is affected). Topic edge: files sharing a topic are mutually coupled
    (a producer's payload change affects its consumers and vice versa), so the edge is
    bidirectional."""
    extra: dict[str, set[str]] = defaultdict(set)

    by_route: dict[str, set[str]] = defaultdict(set)
    for f in index.files:
        for r in getattr(f, "routes_defined", ()) or ():
            by_route[r].add(f.rel)
    for f in index.files:
        for r in getattr(f, "routes_used", ()) or ():
            for definer in by_route.get(r, ()):
                if definer != f.rel:
                    extra[definer].add(f.rel)

    by_topic: dict[str, set[str]] = defaultdict(set)
    for f in index.files:
        for t in getattr(f, "topics", ()) or ():
            by_topic[t].add(f.rel)
    for files in by_topic.values():
        for a in files:
            for b in files:
                if a != b:
                    extra[a].add(b)

    return {k: v for k, v in extra.items() if v}
