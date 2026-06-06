# devagent

A cost-efficient, multi-model coding CLI. A **local model** (e.g. Qwen via llama.cpp) does the
work *inside its parity envelope* — small, well-scoped tasks where it matches a frontier model.
The system keeps every task inside that envelope, verifies every output with a **deterministic
gate**, and consults a **frontier model** only to *decompose* hard tasks or to fix a gate
failure.

> Core bet: a 27B local model won't match a frontier model on a 2000-line file or a huge repo —
> but it's at parity on a small, scoped change. So never hand it a big problem. Decompose,
> retrieve precisely, window large files, gate everything, escalate rarely. The result is
> frontier-quality output at near-zero cost — and both the savings *and* quality are measured.

## No API billing required

Most developers have a **Claude/Max subscription** but won't set up metered API billing. So the
frontier roles spawn the **`claude` CLI** in headless mode (`claude -p`) using your subscription
auth — **zero per-token API billing**. Every exchange is written to `~/.devagent/cli_io/` as an
audit trail. (Metered API and a Codex CLI adapter are also supported — all config, no code.)

## Install (PowerShell)

```powershell
cd C:\Users\ADMIN\devagent
python -m pip install -e .
devagent init        # writes ~/.devagent/config.toml
devagent status      # check models, gate tools, git
```

Optional deterministic-gate tools:

```powershell
python -m pip install -e ".[validate]"   # mypy, ruff, bandit, pytest
```

## Prerequisites

- **Local model**: a llama.cpp server with an OpenAI-compatible API at
  `http://localhost:8080/v1` (configurable), serving e.g. Qwen3 27B.
- **Frontier model**: the `claude` CLI installed and logged in (`claude auth status`). No API
  key needed. (Or set `ANTHROPIC_API_KEY` to use metered API instead — see config.)

## Commands

```powershell
devagent run "<task>"                # decompose → execute locally → gate → apply
  -p, --path <dir>                   #   repo to work in
  -f, --file <path>                  #   target existing file(s) explicitly (repeatable)
      --executor <model>             #   override executor model for this run
      --planner  <model>             #   override planner model for this run
      --dry-run                      #   show intended edits, write nothing
  -y, --yes                          #   skip the keep/rollback confirm
      --audit                        #   after applying, measure parity vs the frontier model

devagent cost                        # cumulative savings (API billing avoided)
devagent quality                     # gate pass rate, in-envelope rate, audited parity rate
devagent audit "<task>" -p <dir>     # one-off differential audit (local vs frontier, judged)
devagent calibrate --init            # write a benchmark template
devagent calibrate                   # map the parity envelope; recommend max_context_tokens
devagent log                         # recent task history
devagent undo [--session <id>]       # roll back a session from its snapshots
devagent resume <session-id>         # continue an interrupted session
devagent status                      # doctor: models, gate tools, git
devagent init                        # create the default config
devagent --version
```

## How a run works

```
index (free, local)
  → retrieve (~3 KB exact context; large files windowed; --file to target)
  → decompose:  in-envelope?  → DIRECT (local only, ~$0)
                otherwise      → frontier model splits into small subtasks
  → per subtask: local execute → deterministic gate (syntax/types/lint/security/tests)
                 gate fails?   → escalate (frontier returns corrected guidance) → re-execute
  → diff → keep / rollback → ledger (cost + quality)
```

Everything is snapshotted; sessions checkpoint per subtask so a crash can `resume`. Escalation
is triggered by a deterministic **gate failure**, never by a model's self-reported confidence.

## Cost & quality, measured

- **`devagent cost`** — actual vs counterfactual (same pipeline, frontier executor). With the
  CLI subscription, marginal cost is `$0` and the CLI's reported `total_cost_usd` becomes the
  **API billing avoided**.
- **`devagent quality`** — objective gate pass rate (the floor) + in-envelope rate + a sampled
  **differential parity rate** (`--audit` / `devagent audit`): the same task on the frontier
  model, compared by a blinded judge. The judge is a *signal*; the gate is the floor.

## Config

`~/.devagent/config.toml` — declare any number of models (three protocols: `openai-compat`,
`anthropic`, `cli`), route by **role** (executor/planner/reviewer/…), set fallback chains, and
tune the **parity envelope** (`max_context_tokens`, `max_file_lines`, `max_subtask_files`).

## Development

```powershell
python -m pip install -e ".[validate]"
python -m pytest        # 37 offline tests (no network, no model calls)
ruff check devagent
```

See `SPEC.md` for the full design, the V1→V5 roadmap, and the reasoning.
