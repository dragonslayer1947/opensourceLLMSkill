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
    no_args_is_help=True,
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


@app.callback()
def _main(
    version: bool = typer.Option(
        None, "--version", "-V", callback=_version_callback, is_eager=True,
        help="Show version and exit."),
):
    """devagent — see `devagent <command> --help` for details."""


@app.command()
def run(
    task: str = typer.Argument(..., help="What to do, in natural language."),
    path: str = typer.Option(".", "--path", "-p", help="Repo to work in."),
    file: list[str] = typer.Option(None, "--file", "-f", help="Target existing file(s) explicitly (repeatable)."),
    executor: str = typer.Option(None, "--executor", help="Override the executor model for this run."),
    planner: str = typer.Option(None, "--planner", help="Override the planner model for this run."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show intended edits, write nothing."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the keep/rollback confirm."),
    audit: bool = typer.Option(False, "--audit", help="After applying, measure parity vs the frontier model (uses the frontier model)."),
    flag: list[str] = typer.Option(None, "--flag", help="Grant a safety-rule flag (e.g. security-review). Repeatable."),
):
    """Decompose a task into in-envelope subtasks, execute locally, gate, and apply."""
    overrides: dict[str, str] = {}
    if executor:
        overrides["executor"] = executor
    if planner:
        overrides["planner"] = planner
    try:
        result = pipeline.run(task, path, dry_run=dry_run, assume_yes=yes, console=console,
                              files=list(file or []), role_overrides=overrides, audit=audit,
                              flags=set(flag or []))
    except RoutingError as e:
        console.print(f"\n[red]model error:[/red] {e}")
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
):
    """List services in the registry (or --init to scaffold one)."""
    from .knowledge import service_registry as sr
    root = Path(path).resolve()
    if init:
        p = sr.write_sample(root)
        console.print(f"sample service at [bold]{p}[/bold]")
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
    console.print(f"  consumes: {', '.join(s.consumes_names) or '—'}")
    console.print(f"  consumed by: {', '.join(sr.downstream_consumers(svcs, name)) or '—'}")
    console.print(f"  events out/in: {', '.join(s.events_produces) or '—'} / "
                  f"{', '.join(s.events_consumes) or '—'}")
    console.print(f"  owns dbs: {', '.join(s.dbs_owned) or '—'}")


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
    path: str = typer.Option(".", "--path", "-p"),
):
    """Capture a pattern (explicit — frontier fixes are not auto-promoted)."""
    from .knowledge import pattern_registry as pr
    p = pr.add_pattern(Path(path).resolve(), name, description, list(tag or []), snippet)
    console.print(f"added pattern [bold]{p.id}[/bold]")


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


if __name__ == "__main__":
    app()
