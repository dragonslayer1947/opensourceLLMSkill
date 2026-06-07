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
      --flag <name>                  #   grant a safety-rule flag (repeatable)
      --contract / --no-contract     #   contract-first for API tasks (default on)
      --review                       #   reviewer agent checks each diff (HIGH finding rolls back)
      --test                         #   run the suite after applying; auto-rollback on failure
      --parallel                     #   run independent subtasks concurrently (file-disjoint waves)

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

# Knowledge & routing (V1.5)
devagent rules [--init]              # safety rules (.devagent/rules.yaml): block/warn/require_flag
devagent services [--init] [--check] # service registry; --check = cross-service contract validation
devagent service <name>              # one service + transitive downstream consumers
devagent adr list|show|new|check     # ADRs; `check` is a semantic diff check via the local model
devagent pattern list|add|deprecate  # learned patterns with confidence decay
devagent contract "<api task>"       # generate + validate an OpenAPI contract (no implementation)

# Multi-service (V2)
devagent contract-diff OLD NEW       # OpenAPI breaking-change diff (pure Python; exit 1 on breaking)

# V3
devagent gen-tests <file>            # draft pytest tests for a source file (local model)

# V4 (institutional knowledge + compliance)
devagent search "<query>"            # three-tier retrieval (exact + BM25 + graph)
devagent compliance                  # compliance profiles (pci-dss / soc2 / hipaa)
devagent incidents [--init]          # recorded incidents (lessons injected when files are touched)
devagent adr set-status <id> <s>     # ADR lifecycle: draft→accepted→deprecated→superseded
devagent pattern add --enforce-glob "**/routes/*.py" --enforce-regex cursor   # write-time enforcement

# V5 (autonomous long-horizon)
devagent epic plan "<goal>"          # decompose a goal into an epic→story→task tree (frontier)
devagent epic show <id>              # the tree with per-node status
devagent epic conflicts <id>         # predict file / import-coupling / reservation conflicts up front
devagent epic run <id> [--max-tasks N]  # run ready tasks via the pipeline, checkpointed + resumable
devagent epic sync <id>              # open one tracker issue per epic/story (null|github|jira|slack)
devagent reserve service:payments --owner team-a   # cross-team reservation (--release to free)
devagent reservations                # list active reservations
devagent propose "<goal>"            # autonomous architecture proposal (human-gated)
devagent propose --approve P-0001    # approve → promote into an enforced ADR
devagent trace [<session>]           # decision trail: routing, context, rules, blast, per-task cost/time
```

The run pipeline (V4): retrieve (cached **three-tier** index) → **route** → **contract-first**
→ decompose → **blast radius** (file + service) → **incident lessons** → **write-locks** →
**parallel waves** → per subtask: **specialized** guidance → execute (ADR + pattern + incident
context) → **safety rules + compliance + migration gate + pattern enforcement** → gate →
escalate → **reviewer** → apply → **conformance** → **test runner** (auto-rollback) → ledger.
A per-session **token/cost budget** can hard-stop it.

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
