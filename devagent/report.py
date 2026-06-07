"""Cost-savings and quality reporting — both first-class outputs.

Cost savings = counterfactual (same pipeline, frontier executor) - actual. Counterfactual is a
labeled ESTIMATE; we under-claim. Quality = objective gate pass rate + in-envelope rate (+ a
sampled differential-audit parity rate when audits have run)."""
from __future__ import annotations

import json

from rich.console import Console
from rich.table import Table

from . import ledger
from .config import Config, Pricing


def cost_of(pricing: dict[str, Pricing], model: str, tin: int, tout: int) -> float:
    p = pricing.get(model, Pricing(0.0, 0.0))
    return (tin * p.input + tout * p.output) / 1_000_000


def counterfactual_cost(pricing: dict[str, Pricing], frontier_model: str, tin: int, tout: int) -> float:
    """What this task would have cost run entirely on the frontier model (same context)."""
    return cost_of(pricing, frontier_model, tin, tout)


def over_budget(calls, limits: dict, pricing: dict[str, Pricing],
                local_ref: str = "sonnet") -> str | None:
    """Return a reason string if the session has hit its token or cost budget, else None.
    A budget of 0 (or missing) means unlimited."""
    token_budget = int(limits.get("token_budget_session", 0) or 0)
    cost_budget = float(limits.get("cost_budget_usd", 0) or 0)
    tokens = sum(c.tin + c.tout for c in calls)
    if token_budget and tokens >= token_budget:
        return f"{tokens} tokens ≥ budget {token_budget}"
    if cost_budget:
        _, counterfactual = billing(calls, pricing, local_ref)
        if counterfactual >= cost_budget:
            return f"${counterfactual:.4f} ≥ budget ${cost_budget:.2f}"
    return None


def billing(calls, pricing: dict[str, Pricing], local_ref: str = "sonnet") -> tuple[float, float]:
    """Return (actual_cost, counterfactual_cost) over a sequence of model calls.

    - cli tier  : marginal $0 (subscription); counterfactual = the API-equivalent cost the CLI
                  reported (total_cost_usd) → this is the API billing avoided.
    - local tier: marginal $0; counterfactual = those tokens priced at a frontier API rate.
    - api tier  : actual = counterfactual = metered price.
    """
    actual = 0.0
    counter = 0.0
    for c in calls:
        if c.tier == "host":
            continue  # host orchestration is incurred in BOTH worlds — see summary() / gap #5
        if c.tier == "cli":
            counter += c.cost_usd
        elif c.tier == "local":
            counter += cost_of(pricing, local_ref, c.tin, c.tout)
        else:
            amt = cost_of(pricing, c.model, c.tin, c.tout)
            actual += amt
            counter += amt
    return actual, counter


def summary(calls, pricing: dict[str, Pricing], local_ref: str = "sonnet") -> dict:
    """Honest end-to-end accounting (gap #5).

    The savings number is EXECUTION-ONLY: it's what frontier *execution* would have billed minus
    what we actually paid. The host model's own orchestration (decomposing, reading diffs,
    verifying) is real frontier work — but it happens in the all-frontier world too, so it nets
    out of *savings*. We surface it separately as `host_*` and compute `pct_local` two ways:
    over execution tokens, and end-to-end including host orchestration."""
    actual, counter = billing(calls, pricing, local_ref)
    exec_tokens = sum(c.tin + c.tout for c in calls if c.tier != "host")
    local_tokens = sum(c.tin + c.tout for c in calls if c.tier == "local")
    host_tokens = sum(c.tin + c.tout for c in calls if c.tier == "host")
    host_cost = sum(c.cost_usd for c in calls if c.tier == "host")
    total_tokens = exec_tokens + host_tokens
    return {
        "actual": actual,
        "counterfactual": counter,
        "savings": counter - actual,
        "exec_tokens": exec_tokens,
        "local_tokens": local_tokens,
        "host_tokens": host_tokens,
        "host_cost": host_cost,
        "host_measured": host_tokens > 0,
        "pct_local_exec": (local_tokens / exec_tokens * 100) if exec_tokens else 100.0,
        "pct_local_end2end": (local_tokens / total_tokens * 100) if total_tokens else 100.0,
    }


def show_cost(config: Config, console: Console) -> None:
    t = ledger.totals(config.db_path)
    if not t or t.get("n", 0) == 0:
        console.print("[dim]No tasks recorded yet. Run `devagent run \"...\"` first.[/dim]")
        return
    actual = t["actual"]
    counter = t["counterfactual"]
    savings = t["savings"]
    host_cost = t.get("host_cost", 0) or 0.0
    pct = (savings / counter * 100) if counter else 0.0

    table = Table(title="Cost savings (cumulative)", show_header=True, header_style="bold")
    table.add_column("Metric")
    table.add_column("Value", justify="right")
    table.add_row("Tasks", str(t["n"]))
    table.add_row("Actual cost (execution)", f"${actual:.4f}")
    table.add_row("If all-frontier execution (est.)", f"${counter:.4f}")
    table.add_row("[green]Saved on execution[/green]", f"[green]${savings:.4f}  ({pct:.1f}%)[/green]")
    if host_cost:
        # Host orchestration is real and is NOT part of the savings above (you pay it either way).
        table.add_row("Host orchestration (not saved)", f"${host_cost:.4f}")
        table.add_row("[bold]Net end-to-end cost[/bold]", f"[bold]${actual + host_cost:.4f}[/bold]")
    console.print(table)
    console.print(r"[dim]Savings are EXECUTION-ONLY: frontier execution avoided vs. actual. The "
                  r"host model's own planning/verifying tokens are real and are NOT counted as "
                  r"savings — record them with `--host-in/--host-out` on a run to see the net "
                  r"end-to-end figure. Counterfactual is an estimate; adjust \[pricing] in config.[/dim]")


def show_quality(config: Config, console: Console) -> None:
    rows = ledger.recent(config.db_path, limit=200)
    if not rows:
        console.print("[dim]No tasks recorded yet.[/dim]")
        return

    n = len(rows)
    gate_pass = 0
    in_env = 0
    for r in rows:
        gate = json.loads(r["quality_gate"] or "{}")
        if gate and all(v != "fail" for v in gate.values()):
            gate_pass += 1
        in_env += int(r["in_envelope"] or 0)

    audit = ledger.audits_summary(config.db_path)

    table = Table(title=f"Quality (last {n} tasks)", show_header=True, header_style="bold")
    table.add_column("Signal")
    table.add_column("Value", justify="right")
    table.add_row("Objective gate pass", f"{gate_pass}/{n}  ({gate_pass / n * 100:.0f}%)")
    table.add_row("In parity envelope", f"{in_env}/{n}  ({in_env / n * 100:.0f}%)")
    if audit["scored"]:
        rate = audit["parity"] / audit["scored"] * 100
        table.add_row("Measured parity (audited)",
                      f"{audit['parity']}/{audit['scored']}  ({rate:.0f}%)")
    else:
        table.add_row("Measured parity (audited)", "[dim]no audits yet — run `devagent audit`[/dim]")
    console.print(table)
    console.print("[dim]Gate = objective floor (types/tests/security). Audit = sampled "
                  "differential vs frontier (LLM judge — a signal, not the floor).[/dim]")
