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
