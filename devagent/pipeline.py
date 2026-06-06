"""The conductor. Implements the parity-envelope flow:

  index -> retrieve -> decompose -> [per subtask: retrieve/window -> Qwen execute -> gate
  -> escalate-on-failure] -> diff -> keep/rollback -> ledger.

Decomposition guarantees the local executor only ever sees small, in-envelope subtasks.
Every write is snapshotted; sessions checkpoint per subtask so a crash can `resume`."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from rich.console import Console

from . import ledger, report
from .config import Config, load_config
from .context.index import build_index
from .context.retrieve import retrieve
from .decompose.planner import Plan, Subtask, decompose
from .execute import apply as ap
from .execute.escalate import get_correction
from .execute.executor import execute_subtask
from .models.registry import Registry
from .models.router import Router, RoutingError
from .validate.gate import GateReport, run_gate


@dataclass
class Call:
    model: str
    tier: str
    tin: int
    tout: int
    cost_usd: float = 0.0


@dataclass
class SubtaskOutcome:
    subtask_id: str
    description: str
    changed_files: list[str]
    gate: dict
    escalated: bool
    status: str  # applied | gate_failed | no_edits


@dataclass
class RunResult:
    session_id: str
    plan: Plan
    outcomes: list[SubtaskOutcome] = field(default_factory=list)
    calls: list[Call] = field(default_factory=list)
    in_envelope: bool = True
    status: str = "applied"


def _session_dir(root: Path, session_id: str) -> Path:
    return root / ".devagent" / "sessions"


def _snap_dir(root: Path, session_id: str, subtask_id: str) -> Path:
    return root / ".devagent" / "snapshots" / session_id / subtask_id


def _save_session(root: Path, result: RunResult, task: str) -> None:
    d = _session_dir(root, result.session_id)
    d.mkdir(parents=True, exist_ok=True)
    payload = {
        "session_id": result.session_id,
        "task": task,
        "status": result.status,
        "subtasks": [
            {"id": s.id, "description": s.description, "target_files": s.target_files,
             "depends_on": s.depends_on}
            for s in result.plan.subtasks
        ],
        "completed": [o.subtask_id for o in result.outcomes if o.status == "applied"],
    }
    (d / f"{result.session_id}.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _run_subtask(
    subtask: Subtask, task_root: Path, config: Config, router: Router,
    index, console: Console, result: RunResult, dry_run: bool,
) -> SubtaskOutcome:
    env = config.envelope
    bundle = retrieve(
        index, subtask.description,
        max_context_tokens=int(env.get("max_context_tokens", 12000)),
        max_file_lines=int(env.get("max_file_lines", 400)),
        max_files=int(env.get("max_subtask_files", 3)) + 1,
        explicit_paths=set(subtask.target_files),
    )
    if not bundle.in_envelope:
        result.in_envelope = False

    out = execute_subtask(subtask, bundle, router)
    result.calls.append(Call(out.model or "?", out.tier or "local", out.tokens_in, out.tokens_out, out.cost_usd))

    prepared = ap.prepare(task_root, out.edits)
    if not prepared.changes:
        console.print(f"  [red]no applicable edits[/red] for {subtask.id} "
                      f"({'; '.join(out.notes) or 'parse/match failure'})")
        return SubtaskOutcome(subtask.id, subtask.description, [], {}, False, "no_edits")

    if dry_run:
        ap.render_diff(prepared.changes, console)
        return SubtaskOutcome(
            subtask.id, subtask.description, [c.path for c in prepared.changes], {}, False, "dry_run")

    snap = _snap_dir(task_root, result.session_id, subtask.id)
    ap.snapshot(task_root, snap, prepared.changes)
    ap.write_changes(task_root, prepared.changes)
    changed = [c.path for c in prepared.changes]

    gate = run_gate(task_root, changed, config.gate)
    escalated = False
    if not gate.passed:
        console.print(f"  [yellow]gate failed[/yellow] on {subtask.id} → escalating")
        guidance, model, tier, tin, tout, cost = get_correction(subtask, bundle, out.raw, gate.render(), router)
        result.calls.append(Call(model or "?", tier or "cli", tin, tout, cost))
        escalated = True
        # roll back the failed attempt, then re-execute with guidance
        ap.undo_from_snapshot(task_root, snap)
        out2 = execute_subtask(subtask, bundle, router, extra_guidance=guidance)
        result.calls.append(Call(out2.model or "?", out2.tier or "local", out2.tokens_in, out2.tokens_out, out2.cost_usd))
        prepared2 = ap.prepare(task_root, out2.edits)
        if prepared2.changes:
            ap.snapshot(task_root, snap, prepared2.changes)
            ap.write_changes(task_root, prepared2.changes)
            changed = [c.path for c in prepared2.changes]
            gate = run_gate(task_root, changed, config.gate)

    status = "applied" if gate.passed else "gate_failed"
    if status == "gate_failed":
        console.print(f"  [red]gate still failing[/red] on {subtask.id}:\n{gate.render()}")
    else:
        console.print(f"  [green]✓[/green] {subtask.id}: {', '.join(changed)}")
    return SubtaskOutcome(subtask.id, subtask.description, changed, gate.to_dict(), escalated, status)


def run(task: str, path: str, *, dry_run: bool, assume_yes: bool, console: Console) -> RunResult:
    config = load_config()
    task_root = Path(path).resolve()
    registry = Registry(config)
    router = Router(registry)

    session_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    result = RunResult(session_id=session_id, plan=Plan([], False, None))

    console.print(f"[bold]Indexing[/bold] {task_root} …")
    index = build_index(task_root)
    console.print(f"  {len(index.files)} files indexed")

    bundle = retrieve(
        index, task,
        max_context_tokens=int(config.envelope.get("max_context_tokens", 12000)),
        max_file_lines=int(config.envelope.get("max_file_lines", 400)),
    )
    if not bundle.views:
        console.print("[yellow]no existing files matched the task[/yellow] — new files will be "
                      "created. Pass --file <path> to target existing code explicitly.")
    console.print(f"[bold]Decomposing[/bold] (retrieved ~{bundle.est_tokens} ctx tokens, "
                  f"in-envelope={bundle.in_envelope}) …")
    plan = decompose(
        task, index, bundle, router,
        max_subtask_files=int(config.envelope.get("max_subtask_files", 3)),
    )
    result.plan = plan
    if plan.decomposed:
        result.calls.append(Call(plan.planner_model or "?", plan.planner_tier or "cli",
                                 plan.tokens_in, plan.tokens_out, plan.cost_usd))
        console.print(f"  decomposed into [bold]{len(plan.subtasks)}[/bold] subtasks "
                      f"(planner: {plan.planner_model})")
    else:
        console.print(f"  in-envelope → [bold]direct[/bold] (1 subtask, no frontier call, ~$0)")

    for st in plan.subtasks:
        console.print(f"\n[bold cyan]» {st.id}[/bold cyan] {st.description}")
        outcome = _run_subtask(st, task_root, config, router, index, console, result, dry_run)
        result.outcomes.append(outcome)
        _save_session(task_root, result, task)

    # keep / rollback
    applied = [o for o in result.outcomes if o.status == "applied"]
    if dry_run:
        result.status = "dry_run"
    elif applied and not assume_yes:
        from rich.prompt import Confirm
        if not Confirm.ask(f"\nKeep {len(applied)} verified change-set(s)?", default=True):
            for o in result.outcomes:
                ap.undo_from_snapshot(task_root, _snap_dir(task_root, session_id, o.subtask_id))
            result.status = "rolled_back"
            console.print("[yellow]rolled back[/yellow]")
        else:
            result.status = "applied"
    else:
        result.status = "applied" if applied else "gate_failed"

    _record(config, task, result)
    _save_session(task_root, result, task)
    return result


def resume_session(session_id: str, path: str, *, assume_yes: bool, console: Console) -> RunResult | None:
    config = load_config()
    task_root = Path(path).resolve()
    sess_file = _session_dir(task_root, session_id) / f"{session_id}.json"
    if not sess_file.exists():
        console.print(f"[red]no session {session_id} under {task_root}[/red]")
        return None
    payload = json.loads(sess_file.read_text(encoding="utf-8"))
    completed = set(payload.get("completed", []))
    subtasks = [
        Subtask(s["id"], s["description"], s.get("target_files", []), s.get("depends_on", []))
        for s in payload.get("subtasks", [])
    ]
    remaining = [s for s in subtasks if s.id not in completed]
    if not remaining:
        console.print(f"[green]session {session_id} already complete[/green]")
        return None

    console.print(f"[bold]Resuming[/bold] {session_id}: {len(remaining)} of {len(subtasks)} "
                  f"subtasks remain")
    registry = Registry(config)
    router = Router(registry)
    index = build_index(task_root)
    result = RunResult(session_id=session_id, plan=Plan(subtasks, True, None))

    for st in remaining:
        console.print(f"\n[bold cyan]» {st.id}[/bold cyan] {st.description}")
        outcome = _run_subtask(st, task_root, config, router, index, console, result, dry_run=False)
        result.outcomes.append(outcome)
        # merge into completed set as we go (durable checkpoint)
        if outcome.status == "applied":
            completed.add(st.id)
            payload["completed"] = sorted(completed)
            sess_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    result.status = "applied" if all(o.status == "applied" for o in result.outcomes) else "gate_failed"
    _record(config, payload.get("task", ""), result)
    return result


def _record(config: Config, task: str, result: RunResult) -> None:
    local_ref = config.reporting.get("local_counterfactual_price", "sonnet")
    tin = sum(c.tin for c in result.calls)
    tout = sum(c.tout for c in result.calls)
    actual, counter = report.billing(result.calls, config.pricing, local_ref)
    files = sorted({f for o in result.outcomes for f in o.changed_files})
    gate_summary: dict = {}
    for o in result.outcomes:
        gate_summary.update(o.gate)
    ledger.log_task(config.db_path, {
        "session_id": result.session_id,
        "task": task,
        "files": files,
        "models_used": sorted({c.model for c in result.calls}),
        "tokens_in": tin,
        "tokens_out": tout,
        "actual_cost": round(actual, 6),
        "counterfactual_cost": round(counter, 6),
        "savings": round(counter - actual, 6),
        "quality_gate": gate_summary,
        "in_envelope": 1 if result.in_envelope else 0,
        "decomposed": 1 if result.plan.decomposed else 0,
        "n_subtasks": len(result.plan.subtasks),
        "audit_result": None,
        "status": result.status,
    })
