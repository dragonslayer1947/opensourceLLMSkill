"""Decompose a task into in-envelope subtasks.

Policy:
- If the task is already small (fits the envelope, touches ~1 file), run it DIRECT — no
  frontier call, ~$0.
- Otherwise the PLANNER role (a frontier model) decomposes it into small, ordered subtasks,
  each scoped to <= max_subtask_files. The frontier model's output is tiny (a plan), so this
  is cheap; its value is that it *creates* the regime where the local model works at parity.

The planner writes the plan only — it never writes implementation."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..context.index import RepoIndex
from ..context.retrieve import ContextBundle
from ..models.router import Router

PLANNER_SYSTEM = """\
You are a senior engineer who DECOMPOSES a coding task into the smallest safe, ordered steps.
You do NOT write code. You output a plan only.

Each step must be small enough for a junior model to implement with a tiny slice of context:
- touch at most {max_files} file(s),
- be a single coherent change,
- have a clear, testable outcome.

CRITICAL — declare shared interfaces in "provides". For every step, list the exact public names
and signatures that step EXPOSES for later steps to call: functions, classes, the import path of
a module-level singleton, route paths, etc. These strings are injected VERBATIM into every
dependent step's prompt, so independently-built pieces call each other with NO drift (the single
biggest cause of "it lints but doesn't fit together"). Be precise and import-accurate, e.g.
  "store: module-level singleton — from app.store import store"
  "class OrderRepo with create(order: Order) -> Order in app/orders/repo.py"
If a step exposes nothing other steps need, use [].

Return ONLY a JSON array. Each element:
  {{"id": "s1", "description": "...", "target_files": ["path/a.py"], "depends_on": [],
    "provides": ["class OrderRepo with create(order: Order) -> Order in app/orders/repo.py"]}}
Order steps so dependencies come first. No prose outside the JSON.
"""


@dataclass
class Subtask:
    id: str
    description: str
    target_files: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    provides: list[str] = field(default_factory=list)  # interfaces this subtask exposes


@dataclass
class Plan:
    subtasks: list[Subtask]
    decomposed: bool          # True if a frontier model was consulted
    planner_model: str | None
    planner_tier: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0


def _repo_skeleton(index: RepoIndex, candidates: list[str], limit: int = 12) -> str:
    """A compact map of the candidate files: path + symbol signatures. Free, local."""
    by_rel = {f.rel: f for f in index.files}
    out = []
    for rel in candidates[:limit]:
        f = by_rel.get(rel)
        if not f:
            continue
        out.append(f"## {rel} ({f.lines} lines)")
        for s in f.symbols[:20]:
            out.append(f"  {s.signature}")
    return "\n".join(out)


def _extract_json_array(text: str) -> list | None:
    fenced = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", text, re.DOTALL)
    raw = fenced.group(1) if fenced else None
    if raw is None:
        m = re.search(r"\[.*\]", text, re.DOTALL)
        raw = m.group(0) if m else None
    if raw is None:
        return None
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else None
    except json.JSONDecodeError:
        return None


def should_decompose(bundle: ContextBundle, max_subtask_files: int) -> bool:
    if not bundle.in_envelope:
        return True
    meaningful = [v for v in bundle.views]
    if len(meaningful) > max_subtask_files:
        return True
    if any(v.windowed for v in bundle.views):
        # A windowed (large) file in play => the change likely spans more than a small slice.
        return True
    return False


def _subs_from_arr(arr: list, task: str, bundle: ContextBundle) -> list[Subtask]:
    subtasks: list[Subtask] = []
    for i, item in enumerate(arr, 1):
        if not isinstance(item, dict):
            continue
        subtasks.append(Subtask(
            id=str(item.get("id") or f"s{i}"),
            description=str(item.get("description", "")).strip(),
            target_files=[str(p) for p in item.get("target_files", []) if p],
            depends_on=[str(d) for d in item.get("depends_on", [])],
            provides=[str(p).strip() for p in item.get("provides", []) or [] if p],
        ))
    return subtasks


def _weak_plan(subtasks: list[Subtask], max_files: int) -> bool:
    """Cheap, deterministic 'is this plan obviously bad?' check — the escalation trigger for
    local-first planning. A weak local plan bounces to the frontier; a sound one is used for free.
    (The full structural/goal-backward check still runs later in the pipeline.)"""
    if not subtasks:
        return True
    ids = [s.id for s in subtasks]
    if len(ids) != len(set(ids)):
        return True
    idset = set(ids)
    for s in subtasks:
        if not s.description:
            return True
        if len(s.target_files) > max_files:
            return True
        if any(d not in idset for d in s.depends_on):
            return True
    return False


def _cache_dir(index: RepoIndex) -> Path:
    return Path(index.root) / ".devagent" / "cache" / "decompose"


def _files_fingerprint(index: RepoIndex, rels: list[str]) -> str:
    """A fingerprint of the candidate files' current content (mtime+size), so a plan is reused only
    while the code it was built against is unchanged (#4) — no stale decompositions on an evolving
    repo. Mirrors the index cache's fingerprint approach."""
    by_rel = {f.rel: f for f in index.files}
    h = hashlib.sha1()
    for rel in sorted(rels):
        f = by_rel.get(rel)
        if not f:
            continue
        try:
            st = f.path.stat()
            h.update(f"{rel}:{st.st_mtime_ns}:{st.st_size};".encode("utf-8"))
        except OSError:
            continue
    return h.hexdigest()[:12]


def _plan_cache_key(task: str, bundle: ContextBundle, max_files: int, index: RepoIndex) -> str:
    raw = (task.strip() + "|" + "|".join(sorted(bundle.candidate_files)) + f"|{max_files}"
           + "|" + _files_fingerprint(index, bundle.candidate_files))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _load_cached_plan(index: RepoIndex, key: str) -> list[Subtask] | None:
    p = _cache_dir(index) / f"{key}.json"
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    subs = _subs_from_arr(data.get("subtasks", []), "", None)  # type: ignore[arg-type]
    return subs or None


def _save_cached_plan(index: RepoIndex, key: str, subtasks: list[Subtask]) -> None:
    try:
        d = _cache_dir(index)
        d.mkdir(parents=True, exist_ok=True)
        payload = {"subtasks": [
            {"id": s.id, "description": s.description, "target_files": s.target_files,
             "depends_on": s.depends_on, "provides": s.provides} for s in subtasks]}
        (d / f"{key}.json").write_text(json.dumps(payload), encoding="utf-8")
    except OSError:
        pass  # caching is best-effort


def decompose(
    task: str,
    index: RepoIndex,
    bundle: ContextBundle,
    router: Router,
    *,
    max_subtask_files: int,
    force_direct: bool = False,
    force_decompose: bool = False,
    prefer_local: bool = True,
    use_cache: bool = True,
) -> Plan:
    if force_direct or (not force_decompose and not should_decompose(bundle, max_subtask_files)):
        return Plan(
            subtasks=[Subtask(id="s1", description=task, target_files=bundle.candidate_files[:1])],
            decomposed=False,
            planner_model=None,
        )

    # Plan cache (#4): an identical task over the same candidate files reuses the decomposition —
    # no planner call, $0. Keyed by content, so it invalidates when the task or file set changes.
    key = _plan_cache_key(task, bundle, max_subtask_files, index)
    if use_cache:
        cached = _load_cached_plan(index, key)
        if cached:
            return Plan(subtasks=cached, decomposed=True, planner_model="(cached)",
                        planner_tier="local")

    skeleton = _repo_skeleton(index, bundle.candidate_files)
    system = PLANNER_SYSTEM.format(max_files=max_subtask_files)
    user = (
        f"TASK:\n{task}\n\n"
        f"RELEVANT FILES (signatures only):\n{skeleton or '(no indexed candidates)'}\n\n"
        f"Decompose into the smallest safe ordered steps."
    )

    def _attempt(role: str):
        try:
            res = router.complete(role, system, user, max_tokens=1500, cacheable_system=True)
        except Exception:  # noqa: BLE001 — a down local planner just means escalate
            return None, None
        arr = _extract_json_array(res.text)
        return res, (_subs_from_arr(arr, task, bundle) if arr else None)

    # Local-first planning (#2): try the local model; escalate to the frontier planner ONLY if the
    # local plan is missing or weak. Decomposition is the costliest frontier call, so this is the
    # biggest saving — and it's safe because the plan is deterministically validated before use.
    result = subtasks = None
    if prefer_local:
        result, subtasks = _attempt("planner_local")
        if subtasks is None or _weak_plan(subtasks, max_subtask_files):
            result, subtasks = _attempt("planner")
    else:
        result, subtasks = _attempt("planner")

    if not subtasks:
        # Planner output unusable -> safe fallback: run the whole task as one subtask.
        return Plan(
            subtasks=[Subtask(id="s1", description=task, target_files=bundle.candidate_files[:1])],
            decomposed=True,
            planner_model=result.model_name if result else None,
            planner_tier=result.tier if result else None,
            tokens_in=result.tokens_in if result else 0,
            tokens_out=result.tokens_out if result else 0,
            cost_usd=result.cost_usd if result else 0.0,
        )

    if use_cache:
        _save_cached_plan(index, key, subtasks)

    return Plan(
        subtasks=subtasks,
        decomposed=True,
        planner_model=result.model_name,
        planner_tier=result.tier,
        tokens_in=result.tokens_in,
        tokens_out=result.tokens_out,
        cost_usd=result.cost_usd,
    )
