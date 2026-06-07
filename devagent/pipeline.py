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
from .execute import contract as contract_mod
from .execute import lock as lock_mod
from .execute.escalate import get_correction
from .execute.executor import execute_subtask
from .knowledge import adr
from .knowledge import pattern_registry
from .knowledge import service_graph, service_registry
from .models.registry import Registry
from .models.router import Router
from .orchestration.classifier import classify
from .planning import blast_radius
from .prove.audit import differential_audit, persist as persist_audit
from .validate import safety_rules
from .validate.gate import run_gate


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
    files: set[str] | None = None,
    rules: list | None = None, flags: set[str] | None = None,
    constraints: str = "",
) -> SubtaskOutcome:
    env = config.envelope
    bundle = retrieve(
        index, subtask.description,
        max_context_tokens=int(env.get("max_context_tokens", 12000)),
        max_file_lines=int(env.get("max_file_lines", 400)),
        max_files=int(env.get("max_subtask_files", 3)) + 1,
        explicit_paths=set(subtask.target_files) | (files or set()),
    )
    if not bundle.in_envelope:
        result.in_envelope = False

    out = execute_subtask(subtask, bundle, router, constraints=constraints)
    result.calls.append(Call(out.model or "?", out.tier or "local", out.tokens_in, out.tokens_out, out.cost_usd))

    prepared = ap.prepare(task_root, out.edits)
    if not prepared.changes:
        console.print(f"  [red]no applicable edits[/red] for {subtask.id} "
                      f"({'; '.join(out.notes) or 'parse/match failure'})")
        return SubtaskOutcome(subtask.id, subtask.description, [], {}, False, "no_edits")

    # Safety rules — evaluated before any write.
    violations = safety_rules.evaluate(prepared.changes, rules or [], flags or set())
    for v in violations:
        tag = "[red]BLOCK[/red]" if v.severity == "block" else "[yellow]warn[/yellow]"
        console.print(f"  {tag} [{v.rule_id}] {v.path}: {v.message}")
    if any(v.severity == "block" for v in violations) and not dry_run:
        console.print(f"  [red]subtask {subtask.id} blocked by safety rules[/red] (not written)")
        return SubtaskOutcome(subtask.id, subtask.description,
                              [c.path for c in prepared.changes], {}, False, "blocked")

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
        out2 = execute_subtask(subtask, bundle, router, extra_guidance=guidance, constraints=constraints)
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


def run(task: str, path: str, *, dry_run: bool, assume_yes: bool, console: Console,
        files: list[str] | None = None, role_overrides: dict[str, str] | None = None,
        audit: bool = False, flags: set[str] | None = None,
        contract: bool = True) -> RunResult:
    config = load_config()
    for role, model in (role_overrides or {}).items():
        config.roles[role] = [model] + [m for m in config.roles.get(role, []) if m != model]
    task_root = Path(path).resolve()
    file_set = set(files or [])
    flags = flags or set()
    rules = safety_rules.load_rules(task_root)
    adrs = adr.load_adrs(task_root)
    constraints = adr.constraints_context(adrs)
    patterns_ctx = pattern_registry.patterns_context(pattern_registry.load_patterns(task_root), task)
    if patterns_ctx:
        constraints = (constraints + "\n\nHOUSE PATTERNS (follow):\n" + patterns_ctx).strip()
    if adr.active(adrs):
        console.print(f"[dim]{len(adr.active(adrs))} active ADR(s) in effect[/dim]")
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
        explicit_paths=file_set,
    )
    if not bundle.views:
        console.print("[yellow]no existing files matched the task[/yellow] — new files will be "
                      "created. Pass --file <path> to target existing code explicitly.")

    # Routing classifier — decide direct vs plan→execute up front (free, deterministic).
    max_ctx = int(config.envelope.get("max_context_tokens", 12000))
    has_pattern = bool(pattern_registry.relevant(
        pattern_registry.load_patterns(task_root), task))
    decision = classify(task, in_envelope=bundle.in_envelope, est_tokens=bundle.est_tokens,
                        max_context_tokens=max_ctx, has_pattern=has_pattern)
    console.print(f"[bold]Routing[/bold]: {decision.route} (score {decision.score}"
                  + (f" — {', '.join(decision.reasons)}" if decision.reasons else "") + ")")

    # Contract-first for API tasks — generate + validate an OpenAPI spec before implementing.
    contract_doc = None
    if contract and contract_mod.is_api_task(task):
        try:
            cr = contract_mod.generate_contract(task, bundle.render(), router)
            result.calls.append(Call(cr.model or "?", cr.tier or "local",
                                     cr.tokens_in, cr.tokens_out, cr.cost_usd))
            if cr.spec and cr.valid:
                contract_doc = cr.spec
                contract_mod.save_contract(task_root, session_id, cr.yaml_text)
                constraints = (constraints + "\n\nAPI CONTRACT (implement exactly):\n"
                               + cr.yaml_text).strip()
                console.print("[green]contract-first[/green]: OpenAPI spec generated + validated")
            else:
                console.print(f"[yellow]contract skipped[/yellow]: {'; '.join(cr.errors) or 'invalid'}")
        except Exception as e:  # noqa: BLE001 — contract-first is best-effort
            console.print(f"[yellow]contract skipped[/yellow]: {e}")

    console.print(f"[bold]Decomposing[/bold] (retrieved ~{bundle.est_tokens} ctx tokens, "
                  f"in-envelope={bundle.in_envelope}) …")
    plan = decompose(
        task, index, bundle, router,
        max_subtask_files=int(config.envelope.get("max_subtask_files", 3)),
        # Classifier may ESCALATE to decomposition; it never suppresses a structurally
        # necessary one (large/windowed/multi-file still decompose via should_decompose).
        force_decompose=(decision.route == "plan_execute"),
    )
    result.plan = plan
    if plan.decomposed:
        result.calls.append(Call(plan.planner_model or "?", plan.planner_tier or "cli",
                                 plan.tokens_in, plan.tokens_out, plan.cost_usd))
        console.print(f"  decomposed into [bold]{len(plan.subtasks)}[/bold] subtasks "
                      f"(planner: {plan.planner_model})")
    else:
        console.print("  in-envelope → [bold]direct[/bold] (1 subtask, no frontier call, ~$0)")

    # Blast radius — impact analysis before any execution.
    planned = sorted(file_set | {f for st in plan.subtasks for f in st.target_files}
                     | set(bundle.candidate_files[:3]))
    if planned:
        br = blast_radius.analyze(
            index, planned,
            warn=int(config.limits.get("blast_radius_warn", 10)),
            block=int(config.limits.get("blast_radius_block", 40)),
        )
        color = {"low": "green", "medium": "yellow", "high": "red"}[br.level]
        console.print(f"[{color}]{br.render()}[/{color}]")

        # Service-level blast radius (V2) — which downstream services may be affected.
        svcs = service_registry.load_services(task_root)
        if svcs:
            for sname in sorted(service_graph.services_for_paths(svcs, planned)):
                down = service_graph.transitive_downstream(svcs, sname)
                if down:
                    console.print(f"[yellow]service blast radius[/yellow]: {sname} → "
                                  f"may affect {', '.join(sorted(down))}")

        if br.level == "high" and not assume_yes and not dry_run:
            from rich.prompt import Confirm
            if not Confirm.ask("High blast radius — proceed?", default=False):
                result.status = "aborted"
                console.print("[yellow]aborted before execution[/yellow]")
                return result

    # Write locks — claim the files this run will touch (released in finally).
    acquired: list[str] = []
    if not dry_run and planned:
        acquired, conflicts = lock_mod.acquire(task_root, planned, session_id)
        if conflicts:
            for p, holder in conflicts:
                console.print(f"[red]locked[/red]: {p} held by session "
                              f"{holder.get('session_id', '?')} (pid {holder.get('pid', '?')})")
            result.status = "locked"
            return result

    try:
        for st in plan.subtasks:
            console.print(f"\n[bold cyan]» {st.id}[/bold cyan] {st.description}")
            outcome = _run_subtask(st, task_root, config, router, index, console, result, dry_run,
                                   file_set, rules, flags, constraints)
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
    finally:
        lock_mod.release(task_root, acquired, session_id)

    # Contract conformance — diff the implementation back against the spec (gap #6).
    if contract_doc is not None and result.status == "applied":
        applied_files = sorted({f for o in result.outcomes if o.status == "applied"
                                for f in o.changed_files})
        code = "\n".join((task_root / f).read_text(encoding="utf-8", errors="replace")
                         for f in applied_files if (task_root / f).exists())
        discrepancies = contract_mod.conformance_check(contract_doc, code)
        if discrepancies:
            console.print("[yellow]contract conformance — implementation diverges:[/yellow]")
            for d in discrepancies[:8]:
                console.print(f"  • {d}")
        else:
            console.print("[green]contract conformance: implementation matches the spec[/green]")

    _record(config, task, result)
    _save_session(task_root, result, task)

    if audit and result.status == "applied":
        console.print("\n[bold]Quality audit[/bold] (local vs frontier)…")
        try:
            ar = differential_audit(task, path, config, registry, router)
            persist_audit(config.db_path, ar, run_kind="audit", run_id=result.session_id)
            if ar.verdict == "skipped":
                console.print(f"  [yellow]skipped[/yellow]: {ar.reason}")
            else:
                console.print(f"  verdict: [bold]{ar.verdict}[/bold] [dim]({ar.reason[:120]})[/dim]")
        except Exception as e:  # noqa: BLE001 — audit is best-effort, never fails the run
            console.print(f"  [yellow]audit failed[/yellow]: {e}")

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
    rules = safety_rules.load_rules(task_root)
    constraints = adr.constraints_context(adr.load_adrs(task_root))
    result = RunResult(session_id=session_id, plan=Plan(subtasks, True, None))

    for st in remaining:
        console.print(f"\n[bold cyan]» {st.id}[/bold cyan] {st.description}")
        outcome = _run_subtask(st, task_root, config, router, index, console, result,
                               dry_run=False, rules=rules, flags=set(), constraints=constraints)
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
