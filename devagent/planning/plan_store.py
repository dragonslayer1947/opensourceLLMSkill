"""Persist a run plan so it can be reviewed, hand-edited, and executed verbatim.

`devagent plan` writes the planner's decomposition here as editable YAML; `devagent run
--from-plan <id>` loads it and executes those exact subtasks with no re-decomposition. This locks
the plan→execute handoff: you (or Claude) decide the breakdown once, inspect it, then the local
executor implements precisely that — the continuity contract for a change."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import yaml

from ..decompose.planner import Plan, Subtask

PLANS_DIR = ".devagent/plans"


def plans_dir(root: Path) -> Path:
    return root / PLANS_DIR


def plan_path(root: Path, plan_id: str) -> Path:
    return plans_dir(root) / f"{plan_id}.yaml"


def new_id() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def save_plan(root: Path, task: str, plan: Plan, plan_id: str | None = None) -> tuple[str, Path]:
    plan_id = plan_id or new_id()
    d = plans_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    payload = {
        "id": plan_id,
        "task": task,
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "planner_model": plan.planner_model,
        "decomposed": plan.decomposed,
        # Edit freely before running: reorder, split, fix target_files / depends_on.
        "subtasks": [
            {"id": s.id, "description": s.description,
             "target_files": s.target_files, "depends_on": s.depends_on}
            for s in plan.subtasks
        ],
    }
    p = plan_path(root, plan_id)
    p.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return plan_id, p


def subtasks_from_data(items) -> list[Subtask]:
    """Build Subtasks from a list of dicts (from YAML on disk or a host-authored JSON plan)."""
    out: list[Subtask] = []
    for i, s in enumerate(items or [], 1):
        if not isinstance(s, dict):
            continue
        out.append(Subtask(
            id=str(s.get("id") or f"s{i}"),
            description=str(s.get("description", "")).strip(),
            target_files=[str(x) for x in s.get("target_files", []) or [] if x],
            depends_on=[str(x) for x in s.get("depends_on", []) or []],
        ))
    return out


def _has_cycle(subtasks: list[Subtask]) -> list[str]:
    """Return the ids involved in a dependency cycle (empty if acyclic)."""
    graph = {s.id: [d for d in s.depends_on] for s in subtasks}
    state: dict[str, int] = {}  # 0=visiting, 1=done
    cycle: list[str] = []

    def visit(node: str, stack: list[str]) -> bool:
        if state.get(node) == 1:
            return False
        if state.get(node) == 0:
            cycle.extend(stack[stack.index(node):] if node in stack else [node])
            return True
        state[node] = 0
        for dep in graph.get(node, []):
            if dep in graph and visit(dep, stack + [node]):
                return True
        state[node] = 1
        return False

    for s in subtasks:
        if state.get(s.id) is None and visit(s.id, []):
            break
    return sorted(set(cycle))


def validate_plan(root: Path, subtasks: list[Subtask], max_files: int = 3) -> list[str]:
    """Structural checks before execution. Returns human-readable issues (empty = clean).

    Catches the ways a hand/host-authored plan goes wrong: duplicate ids, dangling/cyclic
    dependencies, empty descriptions, subtasks over the file envelope, and suspicious paths.
    New files are fine, so a non-existent target_file is NOT an error."""
    issues: list[str] = []
    ids = [s.id for s in subtasks]
    seen: set[str] = set()
    for sid in ids:
        if sid in seen:
            issues.append(f"duplicate subtask id '{sid}'")
        seen.add(sid)
    idset = set(ids)
    for s in subtasks:
        if not s.description:
            issues.append(f"{s.id}: empty description")
        if len(s.target_files) > max_files:
            issues.append(f"{s.id}: touches {len(s.target_files)} files (> envelope of {max_files})")
        for d in s.depends_on:
            if d not in idset:
                issues.append(f"{s.id}: depends_on unknown subtask '{d}'")
        for f in s.target_files:
            norm = f.replace("\\", "/")
            if norm.startswith("/") or ".." in norm.split("/") or (len(norm) > 1 and norm[1] == ":"):
                issues.append(f"{s.id}: target path '{f}' is outside the repo")
    cyc = _has_cycle(subtasks)
    if cyc:
        issues.append(f"dependency cycle among: {', '.join(cyc)}")
    return issues


def load_plan(root: Path, ref: str) -> tuple[str, Plan]:
    """Load a plan by id or by file path. Returns (task, Plan). Raises FileNotFoundError."""
    cand = Path(ref)
    p = cand if (cand.suffix in (".yaml", ".yml") or cand.exists()) else plan_path(root, ref)
    if not p.exists():
        raise FileNotFoundError(f"no plan '{ref}' (looked at {p})")
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    subtasks = subtasks_from_data(data.get("subtasks", []))
    if not subtasks:
        raise ValueError(f"plan '{ref}' has no subtasks")
    plan = Plan(subtasks=subtasks, decomposed=bool(data.get("decomposed", True)),
                planner_model=data.get("planner_model"))
    return str(data.get("task", "")), plan


def list_plans(root: Path) -> list[dict]:
    d = plans_dir(root)
    if not d.exists():
        return []
    out = []
    for p in sorted(d.glob("*.yaml")):
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        out.append({"id": data.get("id", p.stem), "task": data.get("task", ""),
                    "subtasks": len(data.get("subtasks", []) or [])})
    return out
