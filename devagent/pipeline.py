"""The conductor. Implements the parity-envelope flow:

  index -> retrieve -> decompose -> [per subtask: retrieve/window -> Qwen execute -> gate
  -> escalate-on-failure] -> diff -> keep/rollback -> ledger.

Decomposition guarantees the local executor only ever sees small, in-envelope subtasks.
Every write is snapshotted; sessions checkpoint per subtask so a crash can `resume`."""
from __future__ import annotations

import json
import shlex
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from rich.console import Console

from . import ledger, report
from .config import Config, load_config
from .context.cache import build_index_cached
from .context.embed import get_embedder
from .context.retrieve import retrieve
from .decompose.planner import Plan, Subtask, decompose
from .execute import apply as ap
from .execute import contract as contract_mod
from .execute import lock as lock_mod
from .execute import specialized
from .execute.escalate import get_correction
from .execute.executor import execute_subtask
from .knowledge import adr
from .knowledge import compliance
from .knowledge import continuity
from .knowledge import incidents as incidents_mod
from .knowledge import pattern_registry
from .knowledge import service_graph, service_registry
from .models.registry import Registry
from .models.router import Router
from .observability import trace as trace_mod
from .orchestration.classifier import classify
from .planning import blast_radius
from .planning import crosscut
from .planning import plan_check
from .validate import characterize
from .prove.audit import differential_audit, persist as persist_audit
from .review import reviewer as reviewer_mod
from .ui import activity
from .validate import failure_kind
from .validate import impact
from .validate import interface as interface_mod
from .validate import migration_gate
from .validate import safety_rules
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


def _fail_text(gate: GateReport, integration: list[str]) -> str:
    """The text a recovery step reasons over: the failing checks' detail + interface drift."""
    parts = [c.detail for c in gate.failures if c.detail]
    if integration:
        parts.append("INTERFACE:\n" + "\n".join(integration))
    return "\n".join(parts)


def _missing_def_files(index, names: set[str]) -> set[str]:
    """Files that define any of the unresolved names — force-included in the wider retrieval so
    the local model finally SEES the interface it was guessing at."""
    if not names:
        return set()
    want = set(names)
    hits: set[str] = set()
    for f in index.files:
        syms = {s.name.split(".")[-1] for s in getattr(f, "symbols", [])}
        if want & syms:
            hits.add(f.rel)
    return hits


def _retrieve_wider(index, subtask, env, files, embedder, extra_paths):
    """A deliberately bigger slice for a context-miss retry: double the token budget, a few more
    files, and the files that define the unresolved names pinned to the front."""
    return retrieve(
        index, subtask.description,
        max_context_tokens=int(env.get("max_context_tokens", 12000)) * 2,
        max_file_lines=int(env.get("max_file_lines", 400)),
        max_files=int(env.get("max_subtask_files", 3)) + 4,
        explicit_paths=set(subtask.target_files) | (files or set()) | extra_paths,
        embedder=embedder,
    )


def _run_subtask(
    subtask: Subtask, task_root: Path, config: Config, router: Router,
    index, console: Console, result: RunResult, dry_run: bool,
    files: set[str] | None = None,
    rules: list | None = None, flags: set[str] | None = None,
    constraints: str = "", review: bool = False, enforce_patterns: list | None = None,
    use_spinner: bool = True,
) -> SubtaskOutcome:
    env = config.envelope
    embedder = get_embedder(config)  # semantic tier (gap #4) — no-op unless configured
    bundle = retrieve(
        index, subtask.description,
        max_context_tokens=int(env.get("max_context_tokens", 12000)),
        max_file_lines=int(env.get("max_file_lines", 400)),
        max_files=int(env.get("max_subtask_files", 3)) + 1,
        explicit_paths=set(subtask.target_files) | (files or set()),
        embedder=embedder,
    )
    if not bundle.in_envelope:
        result.in_envelope = False

    # Specialized agent: add domain-specific guidance (infra/migration/frontend/api).
    domain, dguide = specialized.guidance_for(subtask.description, subtask.target_files)
    if dguide:
        console.print(f"  [dim]domain: {domain}[/dim]")
        constraints = (constraints + "\n\nDOMAIN GUIDANCE:\n" + dguide).strip()

    with activity(console, f"{subtask.id} · generating edits", enabled=use_spinner):
        out = execute_subtask(subtask, bundle, router, constraints=constraints)
    result.calls.append(Call(out.model or "?", out.tier or "local", out.tokens_in, out.tokens_out, out.cost_usd))

    prepared = ap.prepare(task_root, out.edits)
    if not prepared.changes:
        console.print(f"  [red]no applicable edits[/red] for {subtask.id} "
                      f"({'; '.join(out.notes) or 'parse/match failure'})")
        return SubtaskOutcome(subtask.id, subtask.description, [], {}, False, "no_edits")

    # Safety rules + migration gate — evaluated before any write.
    violations = safety_rules.evaluate(prepared.changes, rules or [], flags or set())
    violations += migration_gate.check(prepared.changes, flags or set())
    violations += pattern_registry.enforce_violations(enforce_patterns or [], prepared.changes)
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

    def _write_and_verify(changes):
        """Write `changes` (caller must undo first if re-writing), then run the per-file gate AND
        the cross-file integration check (#6). Returns (gate, integration_issues, changed_paths)."""
        ap.snapshot(task_root, snap, changes)
        ap.write_changes(task_root, changes)
        paths = [c.path for c in changes]
        with activity(console, f"{subtask.id} · verifying (gate)", enabled=use_spinner):
            g = run_gate(task_root, paths, config.gate)
        # Integration check only matters once a file is individually valid.
        integ = interface_mod.issues_touching(task_root, paths) if g.passed else []
        return g, integ, paths

    gate, integration, changed = _write_and_verify(prepared.changes)
    final_changes = prepared.changes
    escalated = False

    # Recovery #7 — a context-shaped failure (undefined name / unresolved import / interface
    # drift) is a RETRIEVAL miss, not a model limit. Re-retrieve a wider slice (incl. the files
    # that define the missing names) and retry LOCALLY ($0) before any frontier escalation.
    if (not gate.passed or integration) and failure_kind.is_context_failure(
            _fail_text(gate, integration)):
        names = failure_kind.missing_names(_fail_text(gate, integration))
        console.print(f"  [yellow]context-type failure[/yellow] on {subtask.id} → re-retrieving "
                      f"wider and retrying locally" + (f" (missing: {', '.join(sorted(names))})"
                                                       if names else ""))
        wider = _retrieve_wider(index, subtask, env, files, embedder,
                                _missing_def_files(index, names))
        hint = ("Earlier these names were unresolved — use the EXACT definitions now shown in the "
                "context: " + ", ".join(sorted(names))) if names else ""
        ap.undo_from_snapshot(task_root, snap)
        with activity(console, f"{subtask.id} · re-generating with wider context",
                      enabled=use_spinner):
            out_w = execute_subtask(subtask, wider, router, extra_guidance=hint,
                                    constraints=constraints)
        result.calls.append(Call(out_w.model or "?", out_w.tier or "local",
                                 out_w.tokens_in, out_w.tokens_out, out_w.cost_usd))
        prep_w = ap.prepare(task_root, out_w.edits)
        if prep_w.changes:
            gate, integration, changed = _write_and_verify(prep_w.changes)
            final_changes = prep_w.changes
        else:
            ap.undo_from_snapshot(task_root, snap)  # nothing applied — restore originals

    # Frontier escalation — only if local recovery didn't clear the per-file gate.
    if not gate.passed:
        console.print(f"  [yellow]gate failed[/yellow] on {subtask.id} → escalating")
        with activity(console, f"{subtask.id} · asking the frontier model for a fix",
                      enabled=use_spinner):
            guidance, model, tier, tin, tout, cost = get_correction(
                subtask, bundle, out.raw, gate.render(), router)
        result.calls.append(Call(model or "?", tier or "cli", tin, tout, cost))
        escalated = True
        ap.undo_from_snapshot(task_root, snap)  # roll back, then re-execute with guidance
        with activity(console, f"{subtask.id} · re-generating edits", enabled=use_spinner):
            out2 = execute_subtask(subtask, bundle, router, extra_guidance=guidance,
                                   constraints=constraints)
        result.calls.append(Call(out2.model or "?", out2.tier or "local", out2.tokens_in, out2.tokens_out, out2.cost_usd))
        prepared2 = ap.prepare(task_root, out2.edits)
        if prepared2.changes:
            gate, integration, changed = _write_and_verify(prepared2.changes)
            final_changes = prepared2.changes

    if not gate.passed:
        console.print(f"  [red]gate still failing[/red] on {subtask.id}:\n{gate.render()}")
        return SubtaskOutcome(subtask.id, subtask.description, changed, gate.to_dict(),
                              escalated, "gate_failed")

    # #6 — keep the tree GREEN. If cross-file interfaces still don't resolve after recovery, the
    # subtask integrated badly: roll it back rather than leave a half-built, non-importable tree.
    if integration:
        console.print(f"  [red]integration check failed[/red] on {subtask.id} (rolled back):\n  "
                      + "\n  ".join(integration[:5]))
        ap.undo_from_snapshot(task_root, snap)
        return SubtaskOutcome(subtask.id, subtask.description, changed, gate.to_dict(),
                              escalated, "integration_failed")

    # Reviewer agent (V3) — an extra gate: a HIGH-severity finding rolls the subtask back.
    if review:
        with activity(console, f"{subtask.id} · reviewing the diff", enabled=use_spinner):
            findings, meta = reviewer_mod.review_diff(
                subtask.description, ap.unified_diff(final_changes), router)
        if meta:
            result.calls.append(Call(meta["model"] or "?", meta["tier"] or "cli",
                                     meta["tokens_in"], meta["tokens_out"], meta["cost_usd"]))
        for f in findings:
            color = {"high": "red", "medium": "yellow", "low": "dim"}[f.severity]
            console.print(f"  [{color}]review/{f.severity}[/{color}] {f.category}: {f.message}")
        if reviewer_mod.has_blocking(findings):
            ap.undo_from_snapshot(task_root, snap)
            console.print(f"  [red]review blocked {subtask.id}[/red] (rolled back)")
            return SubtaskOutcome(subtask.id, subtask.description, changed, gate.to_dict(),
                                  escalated, "review_failed")

    console.print(f"  [green]✓[/green] {subtask.id}: {', '.join(changed)}")
    return SubtaskOutcome(subtask.id, subtask.description, changed, gate.to_dict(), escalated, "applied")


def run(task: str, path: str, *, dry_run: bool, assume_yes: bool, console: Console,
        files: list[str] | None = None, role_overrides: dict[str, str] | None = None,
        audit: bool = False, flags: set[str] | None = None,
        contract: bool = True, review: bool = False, test: bool = False,
        parallel: bool = False, from_plan: str | None = None,
        host_tokens: tuple[int, int, str] | None = None,
        check_plan: bool = True, characterize_untested: bool = False) -> RunResult:
    config = load_config()
    for role, model in (role_overrides or {}).items():
        config.roles[role] = [model] + [m for m in config.roles.get(role, []) if m != model]
    task_root = Path(path).resolve()
    file_set = set(files or [])
    flags = flags or set()
    rules = safety_rules.load_rules(task_root)
    profiles = config.raw.get("compliance", {}).get("profiles", [])
    if profiles:
        rules += compliance.expand(profiles)
        console.print(f"[dim]compliance profiles: {', '.join(profiles)}[/dim]")
    adrs = adr.load_adrs(task_root)
    constraints = adr.constraints_context(adrs)
    all_patterns = pattern_registry.load_patterns(task_root)
    patterns_ctx = pattern_registry.patterns_context(all_patterns, task)
    if patterns_ctx:
        constraints = (constraints + "\n\nHOUSE PATTERNS (follow):\n" + patterns_ctx).strip()
    if adr.active(adrs):
        console.print(f"[dim]{len(adr.active(adrs))} active ADR(s) in effect[/dim]")
    registry = Registry(config)
    router = Router(registry)

    session_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    result = RunResult(session_id=session_id, plan=Plan([], False, None))
    tr = trace_mod.new_trace(session_id, task)  # decision trail (devagent trace)

    embedder = get_embedder(config)  # semantic retrieval tier (gap #4) — None unless configured
    with activity(console, "Indexing the repo"):
        index = build_index_cached(task_root, embedder=embedder)
    console.print(f"[bold]Indexed[/bold] {len(index.files)} files")
    tr.record("index", files=len(index.files))

    bundle = retrieve(
        index, task,
        max_context_tokens=int(config.envelope.get("max_context_tokens", 12000)),
        max_file_lines=int(config.envelope.get("max_file_lines", 400)),
        explicit_paths=file_set,
        embedder=embedder,
    )
    tr.record("retrieve", est_tokens=bundle.est_tokens, in_envelope=bundle.in_envelope,
              candidates=bundle.candidate_files[:10])
    if profiles:
        tr.record("rules", rules=len(rules), compliance=profiles)
    if not bundle.views:
        console.print("[yellow]no existing files matched the task[/yellow] — new files will be "
                      "created. Pass --file <path> to target existing code explicitly.")

    # Incident knowledge — surface lessons for files this task touches.
    relevant_incidents = incidents_mod.for_files(
        incidents_mod.load_incidents(task_root), bundle.candidate_files)
    if relevant_incidents:
        console.print(f"[yellow]{len(relevant_incidents)} past incident(s)[/yellow] touch these files")
        constraints = (constraints + "\n\nPAST INCIDENTS (do not repeat):\n"
                       + incidents_mod.lessons_context(relevant_incidents)).strip()

    # Continuity memory (gap #6): inject what prior runs changed in these files, so this run
    # builds ON the established interfaces instead of contradicting them.
    cmem = continuity.recent_context(task_root, bundle.candidate_files)
    if cmem:
        console.print("[dim]continuity: recalled prior changes to these files[/dim]")
        constraints = (constraints + "\n\nRECENT CHANGES IN THIS REPO (build on these, do not "
                       "contradict their interfaces):\n" + cmem).strip()

    if from_plan:
        # Execute a previously-reviewed plan verbatim — no routing, no re-decomposition.
        from .planning import plan_store
        loaded_task, plan = plan_store.load_plan(task_root, from_plan)
        if not task:
            task = loaded_task
        result.plan = plan
        contract_doc = None
        console.print(f"[bold]Using saved plan[/bold] [cyan]{from_plan}[/cyan]: "
                      f"{len(plan.subtasks)} subtasks (no re-decomposition)")
        for msg in plan_store.validate_plan(
                task_root, plan.subtasks,
                max_files=int(config.envelope.get("max_subtask_files", 3))):
            console.print(f"[yellow]plan issue[/yellow]: {msg}")
        tr.record("plan_loaded", ref=from_plan, n_subtasks=len(plan.subtasks))
    else:
        # Routing classifier — decide direct vs plan→execute up front (free, deterministic).
        max_ctx = int(config.envelope.get("max_context_tokens", 12000))
        has_pattern = bool(pattern_registry.relevant(
            pattern_registry.load_patterns(task_root), task))
        decision = classify(task, in_envelope=bundle.in_envelope, est_tokens=bundle.est_tokens,
                            max_context_tokens=max_ctx, has_pattern=has_pattern)
        console.print(f"[bold]Routing[/bold]: {decision.route} (score {decision.score}"
                      + (f" — {', '.join(decision.reasons)}" if decision.reasons else "") + ")")
        tr.record("routing", route=decision.route, score=decision.score, reasons=decision.reasons)

        # Contract-first for API tasks — generate + validate an OpenAPI spec before implementing.
        contract_doc = None
        if contract and contract_mod.is_api_task(task):
            try:
                with activity(console, "Drafting + validating the OpenAPI contract"):
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
        with activity(console, "Planning subtasks (consulting the planner)"):
            plan = decompose(
                task, index, bundle, router,
                max_subtask_files=int(config.envelope.get("max_subtask_files", 3)),
                # Classifier may ESCALATE to decomposition; it never suppresses a structurally
                # necessary one (large/windowed/multi-file still decompose via should_decompose).
                force_decompose=(decision.route == "plan_execute"),
            )
        result.plan = plan
        tr.record("decompose", decomposed=plan.decomposed, n_subtasks=len(plan.subtasks),
                  planner=plan.planner_model)
        if plan.decomposed:
            result.calls.append(Call(plan.planner_model or "?", plan.planner_tier or "cli",
                                     plan.tokens_in, plan.tokens_out, plan.cost_usd))
            console.print(f"  decomposed into [bold]{len(plan.subtasks)}[/bold] subtasks "
                          f"(planner: {plan.planner_model})")
        else:
            console.print("  in-envelope → [bold]direct[/bold] (1 subtask, no frontier call, ~$0)")

    # Goal-backward plan check (Tier-1) — is the decomposition COMPLETE and coherent? A missing
    # subtask is silent under-delivery; trusting the plan blindly is the deepest unverified step.
    sgaps = plan_check.structural_gaps(plan.subtasks)          # deterministic, free
    review_gaps: list[str] = []
    if check_plan and len(plan.subtasks) > 1:
        with activity(console, "Checking the plan for missing steps (goal-backward)"):
            review_gaps, pmeta = plan_check.completeness_review(task, plan.subtasks, router)
        if pmeta:
            result.calls.append(Call(pmeta["model"] or "?", pmeta["tier"] or "cli",
                                     pmeta["tokens_in"], pmeta["tokens_out"], pmeta["cost_usd"]))
    for g in sgaps:
        console.print(f"[yellow]plan gap[/yellow]: {g}")
    for g in review_gaps:
        console.print(f"[yellow]possible missing step[/yellow]: {g}")
    tr.record("plan_check", structural=len(sgaps), missing=len(review_gaps))
    if (sgaps or review_gaps) and not assume_yes and not dry_run:
        from rich.prompt import Confirm
        if not Confirm.ask("Plan may be incomplete — proceed anyway?", default=True):
            result.status = "aborted"
            console.print("[yellow]aborted — refine the plan, then re-run[/yellow]")
            return result

    # Cross-cutting change (Tier-1) — a wide rename/signature is ONE intent across many files.
    # Inject a coordination directive so every subtask applies it identically; the green-tree
    # invariant (#6) then rolls back any piece that leaves a dangling reference to the old form.
    cc = crosscut.detect(task)
    if cc:
        renames = ", ".join(f"{o}→{n}" for o, n in cc.renames)
        console.print(f"[cyan]cross-cutting change[/cyan] ({cc.kind})"
                      + (f": {renames}" if renames else "") + " — coordinating all subtasks")
        constraints = (constraints + "\n\n" + cc.directive()).strip()
        tr.record("crosscut", kind=cc.kind, renames=cc.renames)

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
        tr.record("blast_radius", score=br.score, level=br.level, affected=len(br.affected))

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

    # Characterization-test gate (Tier-1) — PIN the current behavior of untested code before we
    # touch it. Generated tests are run against the UNCHANGED code and only kept if they pass (so
    # they describe what the code does today). They then ride the verification below: if a subtask
    # changes that behavior, the pinned test fails and the run is rolled back instead of shipping.
    pinned_tests: list[str] = []
    if characterize_untested and not dry_run:
        from .validate import test_gen, test_runner

        def _gen(src_rel: str, code: str) -> str:
            text, meta = test_gen.generate_tests(src_rel, code, router)
            result.calls.append(Call(meta.get("model") or "?", meta.get("tier") or "local",
                                     meta.get("tokens_in", 0), meta.get("tokens_out", 0),
                                     meta.get("cost_usd", 0.0)))
            return text

        def _runt(test_path: str) -> tuple[bool, str]:
            return test_runner.run_tests(task_root, f"pytest -q {shlex.quote(test_path)}")

        all_targets = sorted({f for st in plan.subtasks for f in st.target_files})
        with activity(console, "Pinning characterization tests for untested code"):
            pins = characterize.pin_all(task_root, index, all_targets, _gen, _runt)
        for r in pins:
            mark = "[green]pinned[/green]" if r.pinned else "[dim]skip[/dim]"
            console.print(f"  {mark} {r.src_rel} — {r.detail}")
        pinned_tests = [r.test_path for r in pins if r.pinned]
        if pinned_tests:
            console.print(f"[bold]{len(pinned_tests)}[/bold] characterization test(s) now pin "
                          f"current behavior")

    local_ref = config.reporting.get("local_counterfactual_price", "sonnet")

    def _budget_hit() -> bool:
        reason = report.over_budget(result.calls, config.limits, config.pricing, local_ref)
        if reason:
            console.print(f"[red]session budget reached[/red]: {reason} — stopping")
        return bool(reason)

    by_id = {s.id: s for s in plan.subtasks}

    def _run_one(st: Subtask, use_spinner: bool = True) -> SubtaskOutcome:
        console.print(f"\n[bold cyan]» {st.id}[/bold cyan] {st.description}")
        t0 = time.monotonic()
        pre = len(result.calls)
        # Inject the interfaces this subtask's (transitive) dependencies declared they provide,
        # so independently-built pieces call each other with the exact agreed signatures (gap #2).
        ifaces = _shared_interfaces(st, by_id)
        cons = constraints
        if ifaces:
            cons = (constraints + "\n\nSHARED INTERFACES (defined by earlier subtasks — use these "
                    "names/signatures EXACTLY):\n" + "\n".join(f"- {i}" for i in ifaces)).strip()
        outcome = _run_subtask(st, task_root, config, router, index, console, result, dry_run,
                               file_set, rules, flags, cons, review, all_patterns,
                               use_spinner=use_spinner)
        new_calls = result.calls[pre:]
        tr.record("subtask", id=st.id, status=outcome.status, files=outcome.changed_files,
                  escalated=outcome.escalated, duration_s=round(time.monotonic() - t0, 3),
                  cost_usd=round(sum(c.cost_usd for c in new_calls), 6),
                  model=next((c.model for c in new_calls), ""))
        return outcome

    try:
        if parallel and len(plan.subtasks) > 1 and not dry_run:
            from concurrent.futures import ThreadPoolExecutor

            from .planning.scheduler import schedule
            waves = schedule(plan.subtasks)
            console.print(f"[bold]Parallel[/bold]: {len(plan.subtasks)} subtasks → {len(waves)} wave(s)")
            for wi, wave in enumerate(waves, 1):
                if _budget_hit():
                    break
                console.print(f"\n[bold]wave {wi}/{len(waves)}[/bold]: {', '.join(s.id for s in wave)}")
                if len(wave) == 1:
                    result.outcomes.append(_run_one(wave[0]))
                else:
                    # One live spinner per wave; per-subtask spinners off (only one live
                    # display may be active, and the »-lines already narrate each subtask).
                    with activity(console, f"wave {wi} · running {len(wave)} subtasks in parallel"), \
                            ThreadPoolExecutor(max_workers=len(wave)) as ex:
                        for outcome in ex.map(lambda s: _run_one(s, use_spinner=False), wave):
                            result.outcomes.append(outcome)
                _save_session(task_root, result, task)
            _consistency_check(result, console)
        else:
            for st in plan.subtasks:
                if _budget_hit():
                    break
                result.outcomes.append(_run_one(st))
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

        # Integration gate (gap #1): run the tests covering the blast radius (changed files +
        # their transitive importers), with auto-rollback. Catches interface drift the per-file
        # lint/type gate cannot see — only running the impacted code does.
        if test and result.status == "applied":
            changed = sorted({f for o in result.outcomes if o.status == "applied"
                              for f in o.changed_files})
            with activity(console, "Impact gate — running tests that cover the change"):
                ires = impact.verify_impact(task_root, changed, index, config.gate)
            detail = (": " + ", ".join(ires.ran)) if ires.ran else ""
            console.print(f"[bold]{ires.render()}[/bold]{detail}")
            tr.record("impact_gate", scope=ires.scope, passed=ires.passed, ran=ires.ran)
            if ires.passed:
                console.print("[green]impact gate passed[/green]")
            else:
                for o in result.outcomes:
                    ap.undo_from_snapshot(task_root, _snap_dir(task_root, session_id, o.subtask_id))
                result.status = "tests_failed"
                console.print(f"[red]impact gate failed → rolled back[/red]\n{ires.output[-700:]}")

        # Characterization verification (Tier-1): did the change preserve the behavior we pinned?
        if pinned_tests and result.status == "applied":
            from .validate import test_runner
            cmd = "pytest -q " + " ".join(shlex.quote(p) for p in pinned_tests)
            with activity(console, "Verifying pinned behavior survived the change"):
                ok, cout = test_runner.run_tests(task_root, cmd)
            if ok:
                console.print("[green]characterization: behavior preserved[/green]")
            else:
                for o in result.outcomes:
                    ap.undo_from_snapshot(task_root, _snap_dir(task_root, session_id, o.subtask_id))
                for p in pinned_tests:
                    (task_root / p).unlink(missing_ok=True)
                result.status = "behavior_changed"
                console.print(f"[red]characterization FAILED — behavior changed → rolled back[/red]"
                              f"\n{cout[-700:]}")

        # Whole-changeset-vs-intent review (gap #8): do the pieces TOGETHER achieve the goal and
        # cohere? Per-subtask review can't see this. A HIGH finding rolls the whole run back.
        applied_outcomes = [o for o in result.outcomes if o.status == "applied"]
        if review and result.status == "applied" and len(applied_outcomes) > 1:
            files = sorted({f for o in applied_outcomes for f in o.changed_files})
            changeset = "\n\n".join(
                f"=== {f} ===\n{(task_root / f).read_text(encoding='utf-8', errors='replace')[:4000]}"
                for f in files if (task_root / f).exists())
            summaries = [f"{o.subtask_id}: {o.description}" for o in applied_outcomes]
            with activity(console, "Reviewing the whole change-set against the goal"):
                cfindings, cmeta = reviewer_mod.review_changeset(task, changeset, summaries, router)
            if cmeta:
                result.calls.append(Call(cmeta["model"] or "?", cmeta["tier"] or "cli",
                                         cmeta["tokens_in"], cmeta["tokens_out"], cmeta["cost_usd"]))
            for fnd in cfindings:
                color = {"high": "red", "medium": "yellow", "low": "dim"}[fnd.severity]
                console.print(f"  [{color}]changeset/{fnd.severity}[/{color}] "
                              f"{fnd.category}: {fnd.message}")
            if reviewer_mod.has_blocking(cfindings):
                for o in result.outcomes:
                    ap.undo_from_snapshot(task_root, _snap_dir(task_root, session_id, o.subtask_id))
                result.status = "changeset_rejected"
                console.print("[red]whole-changeset review: goal not met / pieces don't fit → "
                              "rolled back[/red]")
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

    # Host orchestration accounting (gap #5): if the host (e.g. Claude Code driving the skill)
    # reports its own planning/verifying tokens, record them as a `host`-tier call so cost reports
    # can show the honest end-to-end figure. Host work is NOT counted as savings (incurred either
    # way) — see report.summary / billing.
    if host_tokens:
        h_in, h_out, h_model = host_tokens
        h_model = h_model or config.reporting.get("counterfactual_model", "sonnet")
        h_cost = report.cost_of(config.pricing, h_model, h_in, h_out)
        result.calls.append(Call(h_model, "host", int(h_in), int(h_out), h_cost))
        console.print(f"[dim]host orchestration recorded: {h_in + h_out} tokens "
                      f"≈ ${h_cost:.4f} (not counted as savings)[/dim]")

    _record(config, task, result)
    _save_session(task_root, result, task)

    # Continuity memory (gap #6): record what this run changed so future runs build on it.
    if result.status == "applied":
        continuity.record(
            task_root, task=task,
            files=sorted({f for o in result.outcomes for f in o.changed_files}),
            provides=[p for s in result.plan.subtasks for p in s.provides],
            session_id=session_id)

    if audit and result.status == "applied":
        console.print("\n[bold]Quality audit[/bold] (local vs frontier)…")
        try:
            with activity(console, "Auditing parity vs the frontier model"):
                ar = differential_audit(task, path, config, registry, router)
            persist_audit(config.db_path, ar, run_kind="audit", run_id=result.session_id)
            if ar.verdict == "skipped":
                console.print(f"  [yellow]skipped[/yellow]: {ar.reason}")
            else:
                console.print(f"  verdict: [bold]{ar.verdict}[/bold] [dim]({ar.reason[:120]})[/dim]")
        except Exception as e:  # noqa: BLE001 — audit is best-effort, never fails the run
            console.print(f"  [yellow]audit failed[/yellow]: {e}")

    actual, counter = report.billing(result.calls, config.pricing, local_ref)
    tr.record("final", status=result.status, tokens=sum(c.tin + c.tout for c in result.calls),
              actual_cost=round(actual, 6), counterfactual_cost=round(counter, 6),
              applied=len([o for o in result.outcomes if o.status == "applied"]))
    tr.save(task_root)

    return result


@dataclass
class PlanPreview:
    plan: Plan
    route: str
    est_tokens: int
    blast_level: str = "low"
    blast_score: int = 0


def plan_only(task: str, path: str, *, console: Console,
              files: list[str] | None = None,
              role_overrides: dict[str, str] | None = None) -> PlanPreview:
    """Decomposition-first: index → route → ask the planner (Claude) to decompose, then show the
    subtask plan + blast radius. NOTHING is executed and no local model is needed — this is how
    you see and review the decomposition before handing the pieces to the local executor."""
    config = load_config()
    for role, model in (role_overrides or {}).items():
        config.roles[role] = [model] + [m for m in config.roles.get(role, []) if m != model]
    task_root = Path(path).resolve()
    router = Router(Registry(config))

    embedder = get_embedder(config)
    with activity(console, "Indexing the repo"):
        index = build_index_cached(task_root, embedder=embedder)
    console.print(f"[bold]Indexed[/bold] {len(index.files)} files")

    env = config.envelope
    bundle = retrieve(index, task,
                      max_context_tokens=int(env.get("max_context_tokens", 12000)),
                      max_file_lines=int(env.get("max_file_lines", 400)),
                      explicit_paths=set(files or []),
                      embedder=embedder)

    has_pattern = bool(pattern_registry.relevant(pattern_registry.load_patterns(task_root), task))
    decision = classify(task, in_envelope=bundle.in_envelope, est_tokens=bundle.est_tokens,
                        max_context_tokens=int(env.get("max_context_tokens", 12000)),
                        has_pattern=has_pattern)
    console.print(f"[bold]Routing[/bold]: {decision.route} (score {decision.score})")

    with activity(console, "Decomposing with the planner (Claude) — executing nothing"):
        plan = decompose(task, index, bundle, router,
                         max_subtask_files=int(env.get("max_subtask_files", 3)),
                         force_decompose=(decision.route == "plan_execute"))

    if plan.decomposed:
        console.print(f"\n[bold]Plan[/bold] — {len(plan.subtasks)} subtasks "
                      f"[dim](planner: {plan.planner_model})[/dim]:")
    else:
        console.print("\n[bold]Plan[/bold] — in-envelope; would run as 1 direct subtask "
                      "[dim](no planner call)[/dim]:")
    for st in plan.subtasks:
        files_s = f"  [dim]{', '.join(st.target_files)}[/dim]" if st.target_files else ""
        dep = f"  [dim]⟵ {', '.join(st.depends_on)}[/dim]" if st.depends_on else ""
        console.print(f"  [cyan]{st.id}[/cyan] {st.description}{files_s}{dep}")

    planned = sorted(set(files or []) | {f for st in plan.subtasks for f in st.target_files}
                     | set(bundle.candidate_files[:3]))
    level, score = "low", 0
    if planned:
        br = blast_radius.analyze(index, planned,
                                  warn=int(config.limits.get("blast_radius_warn", 10)),
                                  block=int(config.limits.get("blast_radius_block", 40)))
        level, score = br.level, br.score
        color = {"low": "green", "medium": "yellow", "high": "red"}[br.level]
        console.print(f"[{color}]{br.render()}[/{color}]")

    from .planning import plan_store
    plan_id, plan_path = plan_store.save_plan(task_root, task, plan)
    console.print(f"\n[dim]saved plan[/dim] [cyan]{plan_id}[/cyan] [dim]→ {plan_path}[/dim]")
    console.print(f"[dim]review/edit that file, then execute exactly it:[/dim] "
                  f"devagent run --from-plan {plan_id}")
    return PlanPreview(plan, decision.route, bundle.est_tokens, level, score)


def _shared_interfaces(subtask: Subtask, by_id: dict) -> list[str]:
    """Collect `provides` from a subtask's transitive dependencies (the interfaces it consumes)."""
    out: list[str] = []
    seen: set[str] = set()
    stack = list(subtask.depends_on or [])
    while stack:
        dep_id = stack.pop()
        if dep_id in seen or dep_id not in by_id:
            continue
        seen.add(dep_id)
        dep = by_id[dep_id]
        out.extend(dep.provides or [])
        stack.extend(dep.depends_on or [])
    return out


def _consistency_check(result: RunResult, console: Console) -> None:
    """After parallel execution, verify no file was written by two subtasks (the file-claim
    invariant). The scheduler guarantees this; the check catches any regression (gap #9)."""
    seen: dict[str, list[str]] = {}
    for o in result.outcomes:
        if o.status != "applied":
            continue
        for f in o.changed_files:
            seen.setdefault(f, []).append(o.subtask_id)
    conflicts = {f: ids for f, ids in seen.items() if len(ids) > 1}
    if conflicts:
        console.print("[red]consistency: file written by multiple subtasks:[/red]")
        for f, ids in conflicts.items():
            console.print(f"  {f}: {', '.join(ids)}")


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
    index = build_index_cached(task_root, embedder=get_embedder(config))
    rules = safety_rules.load_rules(task_root)
    constraints = adr.constraints_context(adr.load_adrs(task_root))
    result = RunResult(session_id=session_id, plan=Plan(subtasks, True, None))

    for st in remaining:
        console.print(f"\n[bold cyan]» {st.id}[/bold cyan] {st.description}")
        outcome = _run_subtask(st, task_root, config, router, index, console, result,
                               dry_run=False, rules=rules, flags=set(), constraints=constraints,
                               enforce_patterns=pattern_registry.load_patterns(task_root))
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
    tin = sum(c.tin for c in result.calls if c.tier != "host")
    tout = sum(c.tout for c in result.calls if c.tier != "host")
    actual, counter = report.billing(result.calls, config.pricing, local_ref)
    host_cost = sum(c.cost_usd for c in result.calls if c.tier == "host")
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
        "host_cost": round(host_cost, 6),
    })
