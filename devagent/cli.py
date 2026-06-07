"""devagent CLI (Typer). Runs on PowerShell via the `devagent` entry point or
`python -m devagent`."""
from __future__ import annotations

import json
import shutil
import sys
import urllib.request
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from . import ledger, pipeline, report
from .config import CONFIG_PATH, ensure_config, load_config
from .models.registry import Registry
from .models.router import Router, RoutingError

# Windows consoles may default to cp1252 and choke on the glyphs we print (→, ✓, …).
# Force UTF-8 so the CLI renders correctly in PowerShell / cmd / pipes.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except Exception:  # noqa: BLE001 — older/detached streams: best effort
        pass

app = typer.Typer(
    no_args_is_help=False,   # bare `devagent` opens the interactive shell (see _main)
    add_completion=False,
    help="Cost-efficient multi-model coding CLI. Local model works inside its parity "
         "envelope; a frontier model only decomposes hard tasks or fixes gate failures.",
)
console = Console()


def _version_callback(value: bool):
    if value:
        from . import __version__
        console.print(f"devagent {__version__}")
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def _main(
    ctx: typer.Context,
    version: bool = typer.Option(
        None, "--version", "-V", callback=_version_callback, is_eager=True,
        help="Show version and exit."),
):
    """devagent — run with no command to open the interactive shell, or see
    `devagent <command> --help` for one-shot commands."""
    if ctx.invoked_subcommand is None:
        from . import repl
        repl.run_repl(".")


@app.command()
def run(
    task: str = typer.Argument(None, help="What to do, in natural language. Omit when using --from-plan."),
    path: str = typer.Option(".", "--path", "-p", help="Repo to work in."),
    file: list[str] = typer.Option(None, "--file", "-f", help="Target existing file(s) explicitly (repeatable)."),
    from_plan: str = typer.Option(None, "--from-plan", help="Execute a saved plan id/path verbatim (skip decomposition). See `devagent plan`."),
    executor: str = typer.Option(None, "--executor", help="Override the executor model for this run."),
    planner: str = typer.Option(None, "--planner", help="Override the planner model for this run."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show intended edits, write nothing."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the keep/rollback confirm."),
    audit: bool = typer.Option(False, "--audit", help="After applying, measure parity vs the frontier model (uses the frontier model)."),
    flag: list[str] = typer.Option(None, "--flag", help="Grant a safety-rule flag (e.g. security-review). Repeatable."),
    contract: bool = typer.Option(True, "--contract/--no-contract", help="Contract-first for API tasks (spec → validate → conformance)."),
    review: bool = typer.Option(False, "--review", help="Reviewer agent checks each diff; a HIGH finding rolls it back."),
    test: bool = typer.Option(False, "--test", help="Run the test suite after applying; auto-rollback on failure."),
    parallel: bool = typer.Option(False, "--parallel", help="Run independent subtasks concurrently in dependency-ordered, file-disjoint waves."),
):
    """Decompose a task into in-envelope subtasks, execute locally, gate, and apply.

    With --from-plan, skips decomposition and executes a plan you saved/edited via `devagent plan`."""
    if not task and not from_plan:
        console.print("[yellow]give a task, or --from-plan <id> (see `devagent plan`)[/yellow]")
        raise typer.Exit(2)
    overrides: dict[str, str] = {}
    if executor:
        overrides["executor"] = executor
    if planner:
        overrides["planner"] = planner
    try:
        result = pipeline.run(task or "", path, dry_run=dry_run, assume_yes=yes, console=console,
                              files=list(file or []), role_overrides=overrides, audit=audit,
                              flags=set(flag or []), contract=contract, review=review, test=test,
                              parallel=parallel, from_plan=from_plan)
    except (RoutingError, FileNotFoundError, ValueError) as e:
        console.print(f"\n[red]error:[/red] {e}")
        console.print("[dim]Check `devagent status` — is the local server running and are keys set?[/dim]")
        raise typer.Exit(1)
    _run_summary(result)


def _run_summary(result) -> None:
    cfg = load_config()
    local_ref = cfg.reporting.get("local_counterfactual_price", "sonnet")
    actual, counter = report.billing(result.calls, cfg.pricing, local_ref)
    tin = sum(c.tin for c in result.calls)
    tout = sum(c.tout for c in result.calls)
    local_tok = sum(c.tin + c.tout for c in result.calls if c.tier == "local")
    share = (local_tok / (tin + tout) * 100) if (tin + tout) else 100.0

    console.print(f"\n[bold]Session {result.session_id}[/bold] — status: {result.status}")
    console.print(f"  tokens: {tin + tout}  ({share:.0f}% local)")
    console.print(f"  cost:  ${actual:.4f} actual  vs  ${counter:.4f} all-frontier (est.)  "
                  f"[green]→ saved ${counter - actual:.4f}[/green]")
    applied = [o for o in result.outcomes if o.status == "applied"]
    failed = [o for o in result.outcomes if o.status == "gate_failed"]
    console.print(f"  subtasks: {len(applied)} applied, {len(failed)} gate-failed, "
                  f"{len(result.outcomes)} total")


@app.command()
def plan(
    task: str = typer.Argument(..., help="What to do, in natural language."),
    path: str = typer.Option(".", "--path", "-p", help="Repo to work in."),
    file: list[str] = typer.Option(None, "--file", "-f", help="Target existing file(s) (repeatable)."),
    planner: str = typer.Option(None, "--planner", help="Override the planner model."),
):
    """Decompose a task with the planner (Claude) and show the plan — execute nothing.

    The decomposition-first view: see how the task breaks into small, in-envelope subtasks before
    handing them to the local executor with `devagent run`. Needs no local model."""
    overrides = {"planner": planner} if planner else {}
    try:
        pipeline.plan_only(task, path, console=console, files=list(file or []),
                           role_overrides=overrides or None)
    except RoutingError as e:
        console.print(f"\n[red]model error:[/red] {e}")
        console.print("[dim]the planner needs the `claude` CLI logged in (or a reachable model). "
                      "Check `devagent status`.[/dim]")
        raise typer.Exit(1)


@app.command()
def cost():
    """Show cumulative cost savings (actual vs all-frontier counterfactual)."""
    report.show_cost(load_config(), console)


@app.command()
def quality():
    """Show quality signals: gate pass rate, in-envelope rate, audited parity rate."""
    report.show_quality(load_config(), console)


@app.command(name="log")
def log_cmd(limit: int = typer.Option(15, "--limit", "-n")):
    """Show recent task history."""
    cfg = load_config()
    rows = ledger.recent(cfg.db_path, limit)
    if not rows:
        console.print("[dim]No tasks recorded yet.[/dim]")
        return
    table = Table(show_header=True, header_style="bold")
    for col in ("id", "created_at", "status", "task", "saved$", "local%"):
        table.add_column(col)
    for r in rows:
        tin, tout = r["tokens_in"] or 0, r["tokens_out"] or 0
        table.add_row(
            str(r["id"]), (r["created_at"] or "")[:19], r["status"] or "",
            (r["task"] or "")[:48], f"{r['savings'] or 0:.4f}",
            "—" if not (tin + tout) else f"{int(r['in_envelope'] or 0) * 100}",
        )
    console.print(table)


@app.command()
def undo(
    session: str = typer.Option(None, "--session", "-s", help="Session id (default: latest)."),
    path: str = typer.Option(".", "--path", "-p"),
):
    """Roll back a session's changes from its snapshots."""
    root = Path(path).resolve()
    sess_dir = root / ".devagent" / "sessions"
    if not sess_dir.exists():
        console.print("[yellow]no sessions found here[/yellow]")
        raise typer.Exit(1)
    if session is None:
        files = sorted(sess_dir.glob("*.json"))
        if not files:
            console.print("[yellow]no sessions found[/yellow]")
            raise typer.Exit(1)
        session = files[-1].stem
    payload = json.loads((sess_dir / f"{session}.json").read_text(encoding="utf-8"))
    from .execute.apply import undo_from_snapshot
    restored: list[str] = []
    for st in reversed(payload.get("subtasks", [])):
        snap = root / ".devagent" / "snapshots" / session / st["id"]
        restored += undo_from_snapshot(root, snap)
    console.print(f"Undo {session}: " + (", ".join(restored) if restored else "nothing to restore"))


@app.command()
def resume(
    session_id: str = typer.Argument(..., help="Session id to resume."),
    path: str = typer.Option(".", "--path", "-p"),
    yes: bool = typer.Option(False, "--yes", "-y"),
):
    """Resume an interrupted session from its last completed subtask."""
    result = pipeline.resume_session(session_id, path, assume_yes=yes, console=console)
    if result:
        _run_summary(result)


@app.command()
def services(
    path: str = typer.Option(".", "--path", "-p"),
    init: bool = typer.Option(False, "--init", help="Write a sample service definition and exit."),
    check: bool = typer.Option(False, "--check", help="Diff each produced OpenAPI spec (git HEAD vs working tree) for breaking changes."),
):
    """List services (or --init to scaffold, --check for cross-service contract validation)."""
    from .knowledge import service_registry as sr
    root = Path(path).resolve()
    if init:
        p = sr.write_sample(root)
        console.print(f"sample service at [bold]{p}[/bold]")
        return
    if check:
        _services_check(root)
        return
    svcs = sr.load_services(root)
    if not svcs:
        console.print(f"[dim]no services in {sr.registry_dir(root)} — "
                      f"run `devagent services --init`[/dim]")
        return
    table = Table(title="service registry", show_header=True, header_style="bold")
    for c in ("name", "team", "sla", "consumes", "consumed by"):
        table.add_column(c)
    for s in svcs.values():
        table.add_row(s.name, s.team, s.sla_tier, ", ".join(s.consumes_names) or "—",
                      ", ".join(sr.downstream_consumers(svcs, s.name)) or "—")
    console.print(table)


def _services_check(root: Path) -> None:
    """Cross-service contract validation: diff each produced spec (git HEAD vs working tree)."""
    import subprocess

    import yaml

    from .execute import contract as cm
    from .knowledge import service_graph as sg
    from .knowledge import service_registry as sr

    svcs = sr.load_services(root)
    if not svcs:
        console.print(f"[dim]no services in {sr.registry_dir(root)}[/dim]")
        return
    any_breaking = False
    checked = 0
    for s in svcs.values():
        for spec_rel in sr.produced_spec_paths(s):
            new_path = root / spec_rel
            if not new_path.exists():
                continue
            old = subprocess.run(["git", "show", f"HEAD:{spec_rel}"], cwd=str(root),
                                 capture_output=True, text=True)
            if old.returncode != 0:
                continue  # new spec at HEAD — nothing to diff
            checked += 1
            try:
                old_doc = yaml.safe_load(old.stdout) or {}
                new_doc = yaml.safe_load(new_path.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError:
                continue
            changes = cm.diff_openapi(old_doc, new_doc)
            if changes:
                any_breaking = True
                consumers = sorted(sg.transitive_downstream(svcs, s.name))
                console.print(f"[red]{s.name}[/red] ({spec_rel}): {len(changes)} breaking change(s)"
                              + (f" — affects {', '.join(consumers)}" if consumers else ""))
                for c in changes[:6]:
                    console.print(f"  • [{c.kind}] {c.location}" + (f" — {c.detail}" if c.detail else ""))
    if checked == 0:
        console.print("[dim]no produced specs changed vs HEAD (or git unavailable)[/dim]")
    elif not any_breaking:
        console.print(f"[green]no breaking contract changes[/green] ({checked} spec(s) checked)")


@app.command()
def service(
    name: str = typer.Argument(..., help="Service name to show."),
    path: str = typer.Option(".", "--path", "-p"),
):
    """Show one service's definition and dependency edges."""
    from .knowledge import service_registry as sr
    root = Path(path).resolve()
    svcs = sr.load_services(root)
    s = svcs.get(name)
    if not s:
        console.print(f"[yellow]no service '{name}'[/yellow] (have: {', '.join(svcs) or 'none'})")
        raise typer.Exit(1)
    console.print(f"[bold]{s.name}[/bold]  team={s.team}  sla={s.sla_tier}")
    console.print(f"  tech: {', '.join(s.tech_stack) or '—'}")
    console.print(f"  compliance: {', '.join(s.compliance_zones) or '—'}")
    from .knowledge import service_graph as sg
    console.print(f"  consumes: {', '.join(s.consumes_names) or '—'}")
    console.print(f"  consumed by (direct): {', '.join(sr.downstream_consumers(svcs, name)) or '—'}")
    console.print(f"  downstream (transitive): {', '.join(sorted(sg.transitive_downstream(svcs, name))) or '—'}")
    console.print(f"  events out/in: {', '.join(s.events_produces) or '—'} / "
                  f"{', '.join(s.events_consumes) or '—'}")
    console.print(f"  owns dbs: {', '.join(s.dbs_owned) or '—'}")


@app.command()
def contract(
    task: str = typer.Argument(..., help="API task to draft a contract for."),
    path: str = typer.Option(".", "--path", "-p"),
):
    """Generate and validate an OpenAPI contract for an API task (no implementation)."""
    from .context.index import build_index
    from .context.retrieve import retrieve
    from .execute import contract as cm
    cfg = load_config()
    root = Path(path).resolve()
    idx = build_index(root)
    bundle = retrieve(idx, task,
                      max_context_tokens=int(cfg.envelope.get("max_context_tokens", 12000)),
                      max_file_lines=int(cfg.envelope.get("max_file_lines", 400)))
    router = Router(Registry(cfg))
    try:
        cr = cm.generate_contract(task, bundle.render(), router)
    except RoutingError as e:
        console.print(f"[red]model error:[/red] {e}")
        raise typer.Exit(1)
    if cr.spec and cr.valid:
        console.print("[green]valid OpenAPI contract:[/green]\n")
        console.print(cr.yaml_text)
    else:
        console.print(f"[yellow]invalid/unparseable:[/yellow] {'; '.join(cr.errors)}")
        raise typer.Exit(1)


@app.command(name="contract-diff")
def contract_diff(
    old: str = typer.Argument(..., help="Old OpenAPI YAML/JSON file."),
    new: str = typer.Argument(..., help="New OpenAPI YAML/JSON file."),
):
    """Detect consumer-facing breaking changes between two OpenAPI specs (pure Python)."""
    import yaml
    from .execute import contract as cm
    try:
        old_doc = yaml.safe_load(Path(old).read_text(encoding="utf-8")) or {}
        new_doc = yaml.safe_load(Path(new).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as e:
        console.print(f"[red]could not read specs:[/red] {e}")
        raise typer.Exit(2)
    changes = cm.diff_openapi(old_doc, new_doc)
    if not changes:
        console.print("[green]no breaking changes[/green]")
        return
    console.print(f"[red]{len(changes)} breaking change(s):[/red]")
    for c in changes:
        console.print(f"  • [{c.kind}] {c.location}" + (f" — {c.detail}" if c.detail else ""))
    raise typer.Exit(1)


@app.command(name="gen-tests")
def gen_tests(
    file: str = typer.Argument(..., help="Source file to generate tests for (repo-relative)."),
    path: str = typer.Option(".", "--path", "-p"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Write without confirming."),
):
    """Generate pytest tests for a source file using the local model."""
    import ast as _ast

    from .validate import test_gen
    root = Path(path).resolve()
    src = root / file
    if not src.exists():
        console.print(f"[red]no file:[/red] {file}")
        raise typer.Exit(2)
    cfg = load_config()
    router = Router(Registry(cfg))
    try:
        code, _ = test_gen.generate_tests(file, src.read_text(encoding="utf-8"), router)
    except RoutingError as e:
        console.print(f"[red]model error:[/red] {e}")
        raise typer.Exit(1)
    try:
        _ast.parse(code)
    except SyntaxError as e:
        console.print(f"[yellow]generated tests have a syntax error[/yellow]: {e}")
    dest = root / test_gen.test_path_for(file)
    console.print(f"[bold]{dest.relative_to(root)}[/bold]:\n")
    console.print(code)
    if not yes:
        from rich.prompt import Confirm
        if not Confirm.ask(f"\nWrite {dest.relative_to(root)}?", default=True):
            console.print("[yellow]not written[/yellow]")
            return
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(code, encoding="utf-8")
    console.print(f"[green]wrote {dest.relative_to(root)}[/green]")


@app.command()
def search(
    query: str = typer.Argument(..., help="What to find."),
    path: str = typer.Option(".", "--path", "-p"),
    limit: int = typer.Option(10, "--limit", "-n"),
):
    """Three-tier retrieval (exact + BM25 + graph) — rank files for a query."""
    from .context import rag
    from .context.cache import build_index_cached
    from .planning.blast_radius import build_dependents
    root = Path(path).resolve()
    idx = build_index_cached(root)
    ranked = rag.rank_files(idx, query, dependents=build_dependents(idx), limit=limit)
    if not ranked:
        console.print("[dim]no matches[/dim]")
        return
    for i, rel in enumerate(ranked, 1):
        console.print(f"  {i:>2}. {rel}")


@app.command()
def status(path: str = typer.Option(".", "--path", "-p")):
    """Doctor: config, model reachability, gate tools, git."""
    ensure_config()
    cfg = load_config()
    console.print(f"[bold]config[/bold]: {CONFIG_PATH}")
    console.print(f"[bold]db[/bold]:     {cfg.db_path}")

    mt = Table(title="models", show_header=True, header_style="bold")
    for c in ("name", "protocol", "tier", "status"):
        mt.add_column(c)
    for name, spec in cfg.models.items():
        if spec.protocol == "openai-compat":
            ok = _probe_http(spec.base_url)
            st = "[green]reachable[/green]" if ok else "[red]unreachable[/red]"
        elif spec.protocol == "cli":
            st = ("[green]installed[/green]" if shutil.which(spec.command)
                  else f"[red]'{spec.command}' not found[/red]")
        else:
            st = "[green]key set[/green]" if spec.api_key else "[red]no api key[/red]"
        mt.add_row(name, spec.protocol, spec.tier, st)
    console.print(mt)

    gt = Table(title="gate tools", show_header=True, header_style="bold")
    gt.add_column("tool")
    gt.add_column("status")
    for tool in ("mypy", "ruff", "bandit", "pytest", "git"):
        present = shutil.which(tool) is not None
        gt.add_row(tool, "[green]found[/green]" if present else "[yellow]missing[/yellow]")
    console.print(gt)

    console.print("\n[bold]roles[/bold]:")
    for role, chain in cfg.roles.items():
        console.print(f"  {role}: {' → '.join(chain)}")

    # Execution readiness — the bottleneck for "local does the work, Claude decomposes".
    console.print("\n[bold]execution[/bold]:")
    chain = cfg.role_chain("executor")
    local = next((cfg.models[n] for n in chain
                  if n in cfg.models and cfg.models[n].protocol == "openai-compat"), None)
    if local is None:
        console.print("  no local executor configured — execution would run on a CLI/API model.")
    elif _probe_http(local.base_url):
        console.print(f"  local executor [green]reachable[/green] at {local.base_url} — "
                      f"subtasks run locally (~$0).")
    else:
        console.print(f"  local executor [red]unreachable[/red] at {local.base_url}")
        console.print("  start one →  [bold]ollama serve[/bold] && [bold]ollama pull "
                      "qwen2.5-coder:7b[/bold]  (set base_url=http://localhost:11434/v1, "
                      "model_id=qwen2.5-coder:7b)")
        fallback = next((n for n in chain if n in cfg.models
                         and cfg.models[n].protocol != "openai-compat"), None)
        if fallback:
            console.print(f"  until then, execution falls back to [bold]{fallback}[/bold] "
                          f"(Claude does the work — local% will read 0).")


@app.command()
def incidents(
    path: str = typer.Option(".", "--path", "-p"),
    init: bool = typer.Option(False, "--init", help="Write a sample incident and exit."),
):
    """List recorded incidents (or --init to scaffold one)."""
    from .knowledge import incidents as inc
    root = Path(path).resolve()
    if init:
        p = inc.write_sample(root)
        console.print(f"sample incident at [bold]{p}[/bold]")
        return
    items = inc.load_incidents(root)
    if not items:
        console.print("[dim]no incidents — run `devagent incidents --init`[/dim]")
        return
    table = Table(title="incidents", show_header=True, header_style="bold")
    for c in ("id", "date", "title", "files"):
        table.add_column(c)
    for i in items:
        table.add_row(i.id, i.date, i.title[:40], ", ".join(i.files)[:40])
    console.print(table)


@app.command()
def compliance():
    """List available compliance profiles and which are active in config."""
    from .knowledge import compliance as comp
    cfg = load_config()
    active = cfg.raw.get("compliance", {}).get("profiles", [])
    table = Table(title="compliance profiles", show_header=True, header_style="bold")
    table.add_column("profile")
    table.add_column("rules", justify="right")
    table.add_column("active")
    for name in comp.available():
        on = "[green]yes[/green]" if name in [p.lower() for p in active] else "—"
        table.add_row(name, str(len(comp.PROFILES[name])), on)
    console.print(table)
    console.print("[dim]Enable in config: [compliance] profiles = [\"pci-dss\"][/dim]")


@app.command()
def init():
    """Create ~/.devagent/config.toml if missing and print its path."""
    path = ensure_config()
    console.print(f"config ready at [bold]{path}[/bold]")
    console.print("Edit it to add models, change role chains, or adjust the parity envelope.")


@app.command()
def rules(
    path: str = typer.Option(".", "--path", "-p"),
    init: bool = typer.Option(False, "--init", help="Write a sample .devagent/rules.yaml and exit."),
):
    """Show the safety rules in effect (or --init to scaffold them)."""
    from .validate import safety_rules
    root = Path(path).resolve()
    if init:
        p = safety_rules.write_sample(root)
        console.print(f"sample rules at [bold]{p}[/bold]")
        return
    loaded = safety_rules.load_rules(root)
    if not loaded:
        console.print(f"[dim]no rules at {root / safety_rules.RULES_FILE} — "
                      f"run `devagent rules --init`[/dim]")
        return
    table = Table(title="safety rules", show_header=True, header_style="bold")
    for c in ("id", "action", "match", "flag"):
        table.add_column(c)
    for r in loaded:
        match = r.path_glob or (f"/{r.content_regex}/" if r.content_regex else "")
        table.add_row(r.id, r.action, match[:40], r.flag or "")
    console.print(table)


adr_app = typer.Typer(no_args_is_help=True, help="Architecture Decision Records.")
app.add_typer(adr_app, name="adr")


@adr_app.command("list")
def adr_list(path: str = typer.Option(".", "--path", "-p")):
    """List ADRs and their status."""
    from .knowledge import adr as adr_mod
    adrs = adr_mod.load_adrs(Path(path).resolve())
    if not adrs:
        console.print("[dim]no ADRs — run `devagent adr new`[/dim]")
        return
    table = Table(show_header=True, header_style="bold")
    for c in ("id", "status", "title", "constraints"):
        table.add_column(c)
    for a in adrs:
        status = f"[green]{a.status}[/green]" if a.is_active else a.status
        table.add_row(a.id, status, a.title[:50], str(len(a.constraints)))
    console.print(table)


@adr_app.command("show")
def adr_show(adr_id: str = typer.Argument(...), path: str = typer.Option(".", "--path", "-p")):
    """Show one ADR."""
    from .knowledge import adr as adr_mod
    adrs = {a.id: a for a in adr_mod.load_adrs(Path(path).resolve())}
    a = adrs.get(adr_id)
    if not a:
        console.print(f"[yellow]no ADR '{adr_id}'[/yellow]")
        raise typer.Exit(1)
    console.print(f"[bold]{a.id}[/bold] ({a.status}) — {a.title}")
    console.print(f"  decision: {a.decision}")
    console.print(f"  affects: {', '.join(a.affects_services) or '—'}")
    for c in a.constraints:
        console.print(f"  constraint ({c.severity}): {c.rule}")


@adr_app.command("new")
def adr_new(path: str = typer.Option(".", "--path", "-p")):
    """Scaffold a sample ADR."""
    from .knowledge import adr as adr_mod
    p = adr_mod.write_sample(Path(path).resolve())
    console.print(f"sample ADR at [bold]{p}[/bold]")


@adr_app.command("set-status")
def adr_set_status(
    adr_id: str = typer.Argument(...),
    status: str = typer.Argument(..., help="draft | accepted | deprecated | superseded"),
    path: str = typer.Option(".", "--path", "-p"),
):
    """Transition an ADR's lifecycle status."""
    from .knowledge import adr as adr_mod
    try:
        ok = adr_mod.set_status(Path(path).resolve(), adr_id, status)
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(2)
    console.print(f"[green]{adr_id} → {status}[/green]" if ok else f"[yellow]no ADR '{adr_id}'[/yellow]")
    if not ok:
        raise typer.Exit(1)


@adr_app.command("check")
def adr_check(path: str = typer.Option(".", "--path", "-p")):
    """Check the working-tree git diff against accepted ADRs (semantic, via the local model)."""
    import subprocess
    from .knowledge import adr as adr_mod
    root = Path(path).resolve()
    adrs = adr_mod.load_adrs(root)
    if not adr_mod.active(adrs):
        console.print("[dim]no accepted ADRs to check against[/dim]")
        return
    try:
        diff = subprocess.run(["git", "diff", "HEAD"], cwd=str(root), capture_output=True,
                              text=True, timeout=30).stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        console.print("[yellow]git not available or timed out[/yellow]")
        raise typer.Exit(1)
    if not diff.strip():
        console.print("[dim]no changes to check[/dim]")
        return
    cfg = load_config()
    router = Router(Registry(cfg))
    try:
        violations = adr_mod.check_violations(adrs, diff, router)
    except RoutingError as e:
        console.print(f"[red]model error:[/red] {e}")
        raise typer.Exit(1)
    if not violations:
        console.print("[green]no ADR violations found[/green]")
        return
    for v in violations:
        console.print(f"[red]✗ {v.get('adr_id')}[/red]: {v.get('reason')}")
    raise typer.Exit(1)


pattern_app = typer.Typer(no_args_is_help=True, help="Learned code patterns (with decay).")
app.add_typer(pattern_app, name="pattern")


@pattern_app.command("list")
def pattern_list(path: str = typer.Option(".", "--path", "-p")):
    """List patterns with effective (decayed) confidence."""
    from .knowledge import pattern_registry as pr
    patterns = pr.load_patterns(Path(path).resolve())
    if not patterns:
        console.print("[dim]no patterns — run `devagent pattern add`[/dim]")
        return
    table = Table(show_header=True, header_style="bold")
    for c in ("id", "status", "conf", "eff", "uses", "name"):
        table.add_column(c)
    for p in patterns:
        table.add_row(p.id, p.status, f"{p.confidence:.2f}",
                      f"{pr.effective_confidence(p):.2f}", str(p.uses), p.name[:40])
    console.print(table)


@pattern_app.command("add")
def pattern_add(
    name: str = typer.Argument(..., help="Short pattern name."),
    description: str = typer.Option("", "--desc", "-d"),
    tag: list[str] = typer.Option(None, "--tag", "-t", help="Tag (repeatable)."),
    snippet: str = typer.Option("", "--snippet", "-s"),
    enforce_glob: str = typer.Option("", "--enforce-glob", help="Files matching this glob must contain --enforce-regex."),
    enforce_regex: str = typer.Option("", "--enforce-regex", help="Regex required in matching files."),
    enforce_severity: str = typer.Option("warn", "--enforce-severity", help="warn | block."),
    path: str = typer.Option(".", "--path", "-p"),
):
    """Capture a pattern (explicit — frontier fixes are not auto-promoted)."""
    from .knowledge import pattern_registry as pr
    p = pr.add_pattern(Path(path).resolve(), name, description, list(tag or []), snippet,
                       enforce_glob=enforce_glob, enforce_regex=enforce_regex,
                       enforce_severity=enforce_severity)
    console.print(f"added pattern [bold]{p.id}[/bold]"
                  + (" (enforced)" if enforce_glob and enforce_regex else ""))


@pattern_app.command("deprecate")
def pattern_deprecate(pattern_id: str = typer.Argument(...), path: str = typer.Option(".", "--path", "-p")):
    """Deprecate a pattern so it no longer influences generation."""
    from .knowledge import pattern_registry as pr
    ok = pr.deprecate(Path(path).resolve(), pattern_id)
    console.print(f"deprecated {pattern_id}" if ok else f"[yellow]no pattern '{pattern_id}'[/yellow]")


@app.command()
def calibrate(
    path: str = typer.Option(".", "--path", "-p"),
    file: str = typer.Option(None, "--file", "-f", help="Benchmark TOML (default: .devagent/benchmark.toml)."),
    init: bool = typer.Option(False, "--init", help="Write a benchmark template and exit."),
):
    """Map the parity envelope: run a benchmark on local vs frontier, bucket by context size."""
    from .prove import calibrate as cal
    root = Path(path).resolve()
    if init:
        p = cal.init_benchmark(root)
        console.print(f"benchmark template ready at [bold]{p}[/bold] — add tasks, then "
                      f"`devagent calibrate`.")
        return
    cfg = load_config()
    registry = Registry(cfg)
    router = Router(registry)
    bench = Path(file).resolve() if file else cal.default_benchmark_path(root)
    cal.run_calibration(bench, cfg, registry, router, console)


@app.command()
def audit(
    task: str = typer.Argument(..., help="Task to audit (local vs frontier, judged)."),
    path: str = typer.Option(".", "--path", "-p"),
):
    """Differential quality audit on one task: local vs frontier, blinded judge compares."""
    from .prove.audit import differential_audit, persist
    cfg = load_config()
    registry = Registry(cfg)
    router = Router(registry)
    console.print(f"[bold]Auditing[/bold]: {task}")
    result = differential_audit(task, path, cfg, registry, router)
    persist(cfg.db_path, result, run_kind="audit", run_id=None)
    if result.verdict == "skipped":
        console.print(f"[yellow]skipped[/yellow]: {result.reason}")
        return
    color = {"local_better": "green", "equivalent": "green", "frontier_better": "yellow"}[result.verdict]
    console.print(f"  context: {result.context_tokens} tokens, largest file {result.max_file_lines} lines")
    console.print(f"  verdict: [{color}]{result.verdict}[/{color}]  "
                  f"[dim](local={result.local_model} vs frontier={result.frontier_model})[/dim]")
    console.print(f"  judge: {result.reason}")


def _probe_http(base_url: str | None) -> bool:
    if not base_url:
        return False
    url = base_url.rstrip("/") + "/models"
    try:
        with urllib.request.urlopen(url, timeout=2) as resp:  # noqa: S310
            return resp.status == 200
    except Exception:  # noqa: BLE001
        return False


# ── V5: autonomous long-horizon ────────────────────────────────────────────────────────────

def _repo_skeleton(root: Path, limit: int = 30) -> str:
    """A compact signature map of the repo — fed to the planner for epic/proposal context."""
    from .context.cache import build_index_cached
    index = build_index_cached(root)
    out = []
    for f in index.files[:limit]:
        out.append(f"## {f.rel} ({f.lines} lines)")
        for s in f.symbols[:12]:
            out.append(f"  {s.signature}")
    return "\n".join(out)


epic_app = typer.Typer(no_args_is_help=True, help="Long-horizon epics (epic → story → task).")
app.add_typer(epic_app, name="epic")


@epic_app.command("plan")
def epic_plan(
    goal: str = typer.Argument(..., help="The long-horizon goal to decompose."),
    path: str = typer.Option(".", "--path", "-p"),
):
    """Decompose a goal into an epic → story → task tree (frontier planner) and save it."""
    from .longhorizon import epic as epic_mod
    root = Path(path).resolve()
    cfg = load_config()
    router = Router(Registry(cfg))
    eid = epic_mod.next_epic_id(root)
    console.print(f"[bold]Planning[/bold] {eid}: {goal}")
    try:
        epic = epic_mod.decompose_epic(
            eid, goal, router,
            max_subtask_files=int(cfg.envelope.get("max_subtask_files", 3)),
            skeleton=_repo_skeleton(root))
    except RoutingError as e:
        console.print(f"[red]model error:[/red] {e}")
        raise typer.Exit(1)
    epic_mod.save_epic(root, epic)
    console.print(f"[green]saved[/green] {epic_mod.epic_path(root, eid)} — "
                  f"{len(epic.stories())} stories, {len(epic.tasks())} tasks "
                  f"(planner: {epic.planner_model})")


@epic_app.command("list")
def epic_list(path: str = typer.Option(".", "--path", "-p")):
    """List epics with progress."""
    from .longhorizon import epic as epic_mod
    from .longhorizon import runner
    root = Path(path).resolve()
    epics = epic_mod.list_epics(root)
    if not epics:
        console.print("[dim]no epics — run `devagent epic plan \"<goal>\"`[/dim]")
        return
    table = Table(show_header=True, header_style="bold")
    for c in ("id", "progress", "tasks", "goal"):
        table.add_column(c)
    for e in epics:
        st = runner.load_state(root, e)
        pr = runner.progress(e, st)
        table.add_row(e.id, f"{pr['pct']}%", f"{pr['done']}/{pr['tasks']}", e.goal[:50])
    console.print(table)


@epic_app.command("show")
def epic_show(
    epic_id: str = typer.Argument(...),
    path: str = typer.Option(".", "--path", "-p"),
):
    """Show an epic's tree with per-node status."""
    from .longhorizon import epic as epic_mod
    from .longhorizon import runner
    root = Path(path).resolve()
    epic = epic_mod.load_epic(root, epic_id)
    if not epic:
        console.print(f"[yellow]no epic '{epic_id}'[/yellow]")
        raise typer.Exit(1)
    state = runner.load_state(root, epic)
    _color = {"done": "green", "in_progress": "cyan", "failed": "red",
              "blocked": "yellow", "pending": "dim"}
    console.print(f"[bold]{epic.id}[/bold] — {epic.goal}")
    for story in epic.stories():
        s = runner.status_of(state, story.id)
        console.print(f"  [{_color[s]}]●[/{_color[s]}] {story.id} {story.title} [dim]({s})[/dim]")
        for task in epic.children_of(story.id):
            ts = runner.status_of(state, task.id)
            dep = f" ⟵ {', '.join(task.depends_on)}" if task.depends_on else ""
            console.print(f"      [{_color[ts]}]○[/{_color[ts]}] {task.id} {task.title}"
                          f" [dim]({ts}){dep}[/dim]")


@epic_app.command("conflicts")
def epic_conflicts(
    epic_id: str = typer.Argument(...),
    path: str = typer.Option(".", "--path", "-p"),
):
    """Predict file / coupling / reservation conflicts across the epic's tasks."""
    from .context.cache import build_index_cached
    from .longhorizon import conflict, epic as epic_mod, reservation
    root = Path(path).resolve()
    epic = epic_mod.load_epic(root, epic_id)
    if not epic:
        console.print(f"[yellow]no epic '{epic_id}'[/yellow]")
        raise typer.Exit(1)
    index = build_index_cached(root)
    res = [r.to_dict() for r in reservation.active(root)]
    conflicts = conflict.detect(epic.tasks(), index=index, reservations=res)
    if not conflicts:
        console.print("[green]no predicted conflicts[/green]")
        return
    for c in conflicts:
        color = "red" if c.severity == "block" else "yellow"
        console.print(f"[{color}]{c.render()}[/{color}]")
    if conflict.has_blocking(conflicts):
        raise typer.Exit(1)


@epic_app.command("run")
def epic_run(
    epic_id: str = typer.Argument(...),
    path: str = typer.Option(".", "--path", "-p"),
    max_tasks: int = typer.Option(0, "--max-tasks", help="Stop after N tasks this session (0 = all ready)."),
    review: bool = typer.Option(False, "--review"),
    test: bool = typer.Option(False, "--test"),
):
    """Run the epic's ready tasks via the full pipeline, checkpointing after each (resumable)."""
    from .longhorizon import epic as epic_mod
    from .longhorizon import runner
    root = Path(path).resolve()
    epic = epic_mod.load_epic(root, epic_id)
    if not epic:
        console.print(f"[yellow]no epic '{epic_id}'[/yellow]")
        raise typer.Exit(1)

    def _execute(task) -> tuple[bool, str]:
        try:
            res = pipeline.run(task.description or task.title, str(root), dry_run=False,
                               assume_yes=True, console=console,
                               files=list(task.target_files or []), review=review, test=test)
        except RoutingError as e:
            return False, f"model error: {e}"
        return res.status == "applied", res.status

    def _event(kind: str, detail: dict):
        if kind == "start":
            console.print(f"\n[bold cyan]» {detail['task'].id}[/bold cyan] {detail['task'].title}")
        elif kind == "finish":
            mark = "[green]✓[/green]" if detail["ok"] else "[red]✗[/red]"
            console.print(f"  {mark} {detail['task'].id}: {detail['note']}")

    summary = runner.run_epic(root, epic, _execute,
                              max_tasks=(max_tasks or None), on_event=_event)
    console.print(f"\n[bold]Epic {epic_id}[/bold]: {summary['done']}/{summary['tasks']} tasks done"
                  f" ({summary['pct']}%), {summary['failed']} failed, ran {summary['ran']} this run")


@epic_app.command("sync")
def epic_sync(
    epic_id: str = typer.Argument(...),
    path: str = typer.Option(".", "--path", "-p"),
    provider: str = typer.Option(None, "--provider", help="Override: null | github | jira | slack."),
):
    """Push the epic + stories to the org tracker (one issue each); idempotent."""
    from .integrations import registry, sync
    from .longhorizon import epic as epic_mod
    root = Path(path).resolve()
    epic = epic_mod.load_epic(root, epic_id)
    if not epic:
        console.print(f"[yellow]no epic '{epic_id}'[/yellow]")
        raise typer.Exit(1)
    prov = registry.get_provider(load_config(), root, override=provider)
    console.print(f"[bold]Syncing[/bold] {epic_id} via [bold]{prov.name}[/bold] provider")
    mapping = sync.sync_epic(root, epic, prov)
    for node_id, ref in mapping.items():
        console.print(f"  {node_id} → {ref.get('external_id')} {ref.get('url', '')}")


@app.command()
def reserve(
    resource: str = typer.Argument(..., help="Resource string, e.g. service:payments or file:api.py."),
    owner: str = typer.Option(..., "--owner", "-o", help="Team or person holding the reservation."),
    ttl_hours: float = typer.Option(48, "--ttl", help="Reservation lifetime in hours."),
    note: str = typer.Option("", "--note", "-n"),
    release: bool = typer.Option(False, "--release", help="Release instead of acquire."),
    path: str = typer.Option(".", "--path", "-p"),
):
    """Reserve (or release) a shared resource for cross-team coordination."""
    import time as _time
    from .longhorizon import reservation
    root = Path(path).resolve()
    if release:
        ok = reservation.release(root, resource, owner)
        console.print(f"[green]released[/green] {resource}" if ok
                      else f"[yellow]not released[/yellow] (not held by {owner})")
        return
    res, conflict = reservation.reserve(root, resource, owner, reservation.default_session(),
                                        ttl_seconds=int(ttl_hours * 3600), note=note)
    if conflict:
        expires = _time.strftime("%Y-%m-%d %H:%M", _time.localtime(conflict.expires_at()))
        console.print(f"[red]conflict[/red]: {resource} reserved by "
                      f"[bold]{conflict.owner}[/bold] until {expires}")
        raise typer.Exit(1)
    console.print(f"[green]reserved[/green] {res.resource} for {res.owner} ({ttl_hours:g}h)")


@app.command()
def reservations(path: str = typer.Option(".", "--path", "-p")):
    """List active cross-team reservations."""
    import time as _time
    from .longhorizon import reservation
    root = Path(path).resolve()
    active = reservation.active(root)
    if not active:
        console.print("[dim]no active reservations[/dim]")
        return
    table = Table(show_header=True, header_style="bold")
    for c in ("resource", "owner", "expires", "note"):
        table.add_column(c)
    for r in active:
        table.add_row(r.resource, r.owner,
                      _time.strftime("%Y-%m-%d %H:%M", _time.localtime(r.expires_at())),
                      r.note[:40])
    console.print(table)


@app.command()
def propose(
    goal: str = typer.Argument(None, help="Goal to propose an architecture for (omit with --list/--approve)."),
    path: str = typer.Option(".", "--path", "-p"),
    list_all: bool = typer.Option(False, "--list", help="List proposals and their status."),
    approve: str = typer.Option(None, "--approve", help="Approve proposal id (promotes it to an ADR)."),
    reject: str = typer.Option(None, "--reject", help="Reject proposal id."),
    reviewer: str = typer.Option("", "--reviewer", help="Who approved/rejected."),
):
    """Autonomous architectural proposal behind a human approval gate."""
    from .longhorizon import proposal
    root = Path(path).resolve()

    if list_all:
        props = proposal.load_proposals(root)
        if not props:
            console.print("[dim]no proposals[/dim]")
            return
        table = Table(show_header=True, header_style="bold")
        for c in ("id", "status", "title"):
            table.add_column(c)
        for p in props:
            color = {"approved": "green", "rejected": "red", "proposed": "yellow"}[p.status]
            table.add_row(p.id, f"[{color}]{p.status}[/{color}]", p.title[:60])
        console.print(table)
        return

    if approve or reject:
        pid = approve or reject
        decision = "approved" if approve else "rejected"
        p = proposal.set_decision(root, pid, decision, reviewer)
        if not p:
            console.print(f"[yellow]no proposal '{pid}'[/yellow]")
            raise typer.Exit(1)
        console.print(f"[bold]{pid}[/bold] → {decision}")
        if decision == "approved":
            adr_path = proposal.promote_to_adr(root, p)
            console.print(f"[green]promoted to ADR[/green]: {adr_path}")
        return

    if not goal:
        console.print("[yellow]give a goal, or use --list / --approve / --reject[/yellow]")
        raise typer.Exit(2)
    cfg = load_config()
    router = Router(Registry(cfg))
    console.print(f"[bold]Proposing[/bold] architecture for: {goal}")
    try:
        p = proposal.propose(root, goal, router, skeleton=_repo_skeleton(root))
    except RoutingError as e:
        console.print(f"[red]model error:[/red] {e}")
        raise typer.Exit(1)
    console.print(f"[bold]{p.id}[/bold] ({p.status}) — {p.title}")
    console.print(f"  decision: {p.decision}")
    for c in p.constraints:
        console.print(f"  constraint ({c.get('severity', 'warn')}): {c.get('rule', '')}")
    console.print(f"[dim]approve with: devagent propose --approve {p.id}[/dim]")


@app.command()
def trace(
    session: str = typer.Argument(None, help="Session id (default: latest)."),
    path: str = typer.Option(".", "--path", "-p"),
):
    """Show the decision trail for a run: routing, context, rules, blast radius, per-subtask cost/time."""
    from .observability import trace as trace_mod
    root = Path(path).resolve()
    sid = session or trace_mod.latest(root)
    if not sid:
        console.print("[dim]no traces recorded yet[/dim]")
        return
    data = trace_mod.load_trace(root, sid)
    if not data:
        console.print(f"[yellow]no trace for session '{sid}'[/yellow]")
        raise typer.Exit(1)
    console.print(f"[bold]trace {sid}[/bold] — {data.get('task', '')[:70]}")
    for e in data.get("events", []):
        d = e.get("detail", {})
        kv = ", ".join(f"{k}={v}" for k, v in d.items() if k not in ("files", "candidates"))
        console.print(f"  [dim]{e.get('elapsed_s', 0):>6.2f}s[/dim] [cyan]{e.get('kind')}[/cyan]  {kv}")
    summ = trace_mod.summarize(data)
    console.print(f"[bold]subtasks[/bold]: {len(summ['subtasks'])}  "
                  f"blast={summ['blast_radius'].get('level', '—')}  "
                  f"total cost ${summ['total_cost']:.4f}")


if __name__ == "__main__":
    app()
