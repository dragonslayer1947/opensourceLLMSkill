"""Decompose a task into in-envelope subtasks.

Policy:
- If the task is already small (fits the envelope, touches ~1 file), run it DIRECT — no
  frontier call, ~$0.
- Otherwise the PLANNER role (a frontier model) decomposes it into small, ordered subtasks,
  each scoped to <= max_subtask_files. The frontier model's output is tiny (a plan), so this
  is cheap; its value is that it *creates* the regime where the local model works at parity.

The planner writes the plan only — it never writes implementation."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

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

Return ONLY a JSON array. Each element:
  {{"id": "s1", "description": "...", "target_files": ["path/a.py"], "depends_on": []}}
Order steps so dependencies come first. No prose outside the JSON.
"""


@dataclass
class Subtask:
    id: str
    description: str
    target_files: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)


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


def decompose(
    task: str,
    index: RepoIndex,
    bundle: ContextBundle,
    router: Router,
    *,
    max_subtask_files: int,
    force_direct: bool = False,
    force_decompose: bool = False,
) -> Plan:
    if force_direct or (not force_decompose and not should_decompose(bundle, max_subtask_files)):
        return Plan(
            subtasks=[Subtask(id="s1", description=task, target_files=bundle.candidate_files[:1])],
            decomposed=False,
            planner_model=None,
        )

    skeleton = _repo_skeleton(index, bundle.candidate_files)
    system = PLANNER_SYSTEM.format(max_files=max_subtask_files)
    user = (
        f"TASK:\n{task}\n\n"
        f"RELEVANT FILES (signatures only):\n{skeleton or '(no indexed candidates)'}\n\n"
        f"Decompose into the smallest safe ordered steps."
    )
    result = router.complete("planner", system, user, max_tokens=1500, cacheable_system=True)
    arr = _extract_json_array(result.text)

    if not arr:
        # Planner output unusable -> safe fallback: run the whole task as one subtask.
        return Plan(
            subtasks=[Subtask(id="s1", description=task, target_files=bundle.candidate_files[:1])],
            decomposed=True,
            planner_model=result.model_name,
            planner_tier=result.tier,
            tokens_in=result.tokens_in,
            tokens_out=result.tokens_out,
            cost_usd=result.cost_usd,
        )

    subtasks = []
    for i, item in enumerate(arr, 1):
        if not isinstance(item, dict):
            continue
        subtasks.append(Subtask(
            id=str(item.get("id") or f"s{i}"),
            description=str(item.get("description", "")).strip(),
            target_files=[str(p) for p in item.get("target_files", []) if p],
            depends_on=[str(d) for d in item.get("depends_on", [])],
        ))
    if not subtasks:
        subtasks = [Subtask(id="s1", description=task, target_files=bundle.candidate_files[:1])]

    return Plan(
        subtasks=subtasks,
        decomposed=True,
        planner_model=result.model_name,
        planner_tier=result.tier,
        tokens_in=result.tokens_in,
        tokens_out=result.tokens_out,
        cost_usd=result.cost_usd,
    )
