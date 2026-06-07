"""Cross-service dependency graph and service-level blast radius (V2).

Built from the service registry's `consumes` edges. `transitive_downstream(name)` answers the
question that matters for impact analysis: if service `name` changes its API, which services
(transitively) break? `service_for_path` maps a changed file to its owning service via `root`."""
from __future__ import annotations

from collections import deque

from .service_registry import Service


def build_graph(services: dict[str, Service]) -> dict[str, set[str]]:
    """name -> set of services it consumes (that exist in the registry)."""
    known = set(services)
    return {name: {c for c in s.consumes_names if c in known} for name, s in services.items()}


def _reverse(graph: dict[str, set[str]]) -> dict[str, set[str]]:
    """name -> set of services that consume it (its direct downstream)."""
    rev: dict[str, set[str]] = {n: set() for n in graph}
    for consumer, consumed in graph.items():
        for dep in consumed:
            rev.setdefault(dep, set()).add(consumer)
    return rev


def transitive_downstream(services: dict[str, Service], name: str) -> set[str]:
    rev = _reverse(build_graph(services))
    seen: set[str] = set()
    queue: deque[str] = deque(rev.get(name, set()))
    while queue:
        node = queue.popleft()
        if node in seen or node == name:
            continue
        seen.add(node)
        queue.extend(rev.get(node, set()))
    return seen


def service_for_path(services: dict[str, Service], rel_path: str) -> str | None:
    """The service whose `root` is the longest prefix of rel_path."""
    rel = rel_path.replace("\\", "/")
    best: tuple[int, str | None] = (-1, None)
    for name, s in services.items():
        root = (s.root or "").replace("\\", "/").rstrip("/")
        if root and (rel == root or rel.startswith(root + "/")) and len(root) > best[0]:
            best = (len(root), name)
    return best[1]


def services_for_paths(services: dict[str, Service], paths: list[str]) -> set[str]:
    out = set()
    for p in paths:
        s = service_for_path(services, p)
        if s:
            out.add(s)
    return out
