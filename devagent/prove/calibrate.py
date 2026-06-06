"""Calibrate the parity envelope. Runs a benchmark suite through the differential audit, then
buckets results by context size to find where the local model holds parity. Recommends a
`max_context_tokens` threshold = the top of the largest bucket that still clears the target."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.table import Table

from .. import ledger
from ..config import Config
from ..models.registry import Registry
from ..models.router import Router
from .audit import differential_audit, persist

BENCHMARK_TEMPLATE = """\
# devagent calibration benchmark.
# Each task is run on BOTH the local and frontier model; a judge compares them. The result
# maps where the local model holds parity, so routing thresholds are data-driven.
# Include a spread of sizes (small single-file edits up to large-file / multi-file changes).

[[task]]
repo = "."
description = "example: add input validation to the create endpoint"
tags = ["small", "api"]

# [[task]]
# repo = "C:/path/to/another/repo"
# description = "refactor the database layer to use connection pooling"
# tags = ["large", "infra"]
"""

# Context-token buckets used to map the envelope.
BUCKETS = [(0, 2000), (2000, 6000), (6000, 12000), (12000, 10_000_000)]


@dataclass
class BenchTask:
    repo: str
    description: str
    tags: list[str]


def default_benchmark_path(root: Path) -> Path:
    return root / ".devagent" / "benchmark.toml"


def init_benchmark(root: Path) -> Path:
    path = default_benchmark_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(BENCHMARK_TEMPLATE, encoding="utf-8")
    return path


def load_benchmark(path: Path) -> list[BenchTask]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    tasks = []
    for t in data.get("task", []):
        tasks.append(BenchTask(
            repo=t.get("repo", "."),
            description=t["description"],
            tags=[str(x) for x in t.get("tags", [])],
        ))
    return tasks


def run_calibration(
    benchmark_path: Path, config: Config, registry: Registry, router: Router, console: Console,
) -> str | None:
    if not benchmark_path.exists():
        console.print(f"[yellow]no benchmark at {benchmark_path}[/yellow] — run "
                      f"`devagent calibrate --init` to create one.")
        return None
    tasks = load_benchmark(benchmark_path)
    if not tasks:
        console.print("[yellow]benchmark has no tasks[/yellow]")
        return None

    run_id = "cal-" + datetime.now().strftime("%Y%m%d-%H%M%S")
    console.print(f"[bold]Calibrating[/bold] on {len(tasks)} task(s) — run {run_id}")
    skipped = 0
    for i, t in enumerate(tasks, 1):
        console.print(f"  [{i}/{len(tasks)}] {t.description[:60]} [dim]({t.repo})[/dim]")
        result = differential_audit(t.description, t.repo, config, registry, router,
                                    run_kind="calibrate", run_id=run_id)
        persist(config.db_path, result, run_kind="calibrate", run_id=run_id)
        if result.verdict == "skipped":
            skipped += 1
            console.print(f"      [yellow]skipped[/yellow]: {result.reason}")
        else:
            console.print(f"      ctx={result.context_tokens}tok  verdict=[bold]{result.verdict}[/bold]")

    if skipped == len(tasks):
        console.print("[red]all tasks skipped[/red] — no frontier model available "
                      "(set ANTHROPIC_API_KEY or configure a cloud model).")
        return run_id

    _report(config, run_id, console)
    return run_id


def _report(config: Config, run_id: str, console: Console) -> None:
    rows = ledger.audits_by_bucket(config.db_path, run_id, BUCKETS)
    target = float(config.reporting.get("parity_target", 0.9))

    table = Table(title=f"Parity by context size (target ≥ {target:.0%})",
                  show_header=True, header_style="bold")
    table.add_column("context tokens")
    table.add_column("n", justify="right")
    table.add_column("parity", justify="right")
    table.add_column("rate", justify="right")

    recommended = 0
    contiguous_ok = True
    for b in rows:
        hi_label = "∞" if b["hi"] >= 10_000_000 else str(b["hi"])
        if b["total"] == 0:
            table.add_row(f"{b['lo']}–{hi_label}", "0", "—", "[dim]no data[/dim]")
            contiguous_ok = False
            continue
        rate = b["parity"] / b["total"]
        ok = rate >= target
        mark = "[green]✓[/green]" if ok else "[red]✗[/red]"
        table.add_row(f"{b['lo']}–{hi_label}", str(b["total"]), str(b["parity"]),
                      f"{mark} {rate:.0%}")
        if ok and contiguous_ok:
            recommended = b["hi"] if b["hi"] < 10_000_000 else recommended
        else:
            contiguous_ok = False

    console.print(table)
    if recommended:
        console.print(f"\n[bold]Recommended[/bold] envelope.max_context_tokens ≈ "
                      f"[green]{recommended}[/green] "
                      f"(largest bucket still at parity). Set it in {config.db_path.parent / 'config.toml'}.")
    else:
        console.print("\n[yellow]No bucket cleared the target.[/yellow] Tighten the envelope "
                      "(smaller max_context_tokens / max_file_lines) or improve retrieval, then re-run.")
    console.print("[dim]Verdicts come from an LLM judge — a signal, not the gate. "
                  "Add more benchmark tasks for a sharper map.[/dim]")
