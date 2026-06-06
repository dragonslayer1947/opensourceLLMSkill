# devagent

A cost-efficient multi-model coding CLI. A **local model** (Qwen via llama.cpp) does the work
*inside its parity envelope* — small, well-scoped tasks where it matches a frontier model. The
system's job is to keep every task inside that envelope, verify every output with a
deterministic gate, and consult a **frontier model** (Claude, etc.) only to *decompose* hard
tasks or to fix a gate failure.

> Core bet: a 27B local model won't match Opus on a 2000-line file or a huge repo — but it's at
> parity on a small, scoped change. So never hand it a big problem. Decompose, retrieve
> precisely, window large files, gate everything, escalate rarely. Result: frontier-quality
> output at near-zero cost — and the savings *and* quality are measured.

## Install (PowerShell)

```powershell
cd C:\Users\ADMIN\devagent
python -m pip install -e .
devagent init        # writes ~/.devagent/config.toml
devagent status      # check models, gate tools, git
```

For the deterministic gate, install the optional tools:

```powershell
python -m pip install -e ".[validate]"   # mypy, ruff, bandit, pytest
```

## Prerequisites

- **Local model**: a llama.cpp server with an OpenAI-compatible API at
  `http://localhost:8080/v1` (configurable). Serving Qwen3 27B, for example.
- **Frontier model (optional)**: set `ANTHROPIC_API_KEY`. Without it, the `planner`/`reviewer`
  roles fall back to the local model (config-driven).

## Use

```powershell
devagent run "add cursor pagination to the product list endpoint"
devagent run "rename get_user to fetch_user across the service" --dry-run
devagent run "..." --yes            # skip the keep/rollback confirm
devagent cost                       # cumulative savings (actual vs all-frontier)
devagent quality                    # gate pass rate, in-envelope rate, audited parity
devagent log                        # recent task history
devagent undo                       # roll back the last session
devagent resume 20260607-101500     # continue an interrupted session
```

## How a run works

```
index (free, local)
  → retrieve (~3 KB exact context; large files windowed)
  → decompose:  in-envelope?  → DIRECT (local only, ~$0)
                otherwise      → frontier model splits into small subtasks
  → per subtask: local execute → deterministic gate
                 gate fails?   → escalate (frontier returns corrected guidance) → re-execute
  → diff → keep / rollback → ledger (cost + quality)
```

Everything is snapshotted; sessions checkpoint per subtask so a crash can `resume`.

## Config

`~/.devagent/config.toml` — declare any number of models (two protocols cover almost
everything: `openai-compat` and `anthropic`), route by **role**, set fallback chains, and tune
the **parity envelope** (`max_context_tokens`, `max_file_lines`, `max_subtask_files`).

See `SPEC.md` for the full design, the V1→V5 roadmap, and the reasoning.
