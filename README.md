# devagent — an open-source local-LLM coding skill

> **Add this to any CLI as a skill — Codex, Gemini, Claude Code CLI, etc.** It breaks down your
> main task and helps you utilise your GPU power to reduce the cost of development.

The skill lives in [`skills/decompose-execute/`](skills/decompose-execute/SKILL.md): the host
model (your CLI's frontier model) **decomposes** a task into small, well-scoped pieces and routes
the token-heavy code generation to a **local model** via the `devagent` engine described below — a
deterministic gate verifies every piece. The bulk of generation runs on your own GPU at ~$0.

---

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

## Use it as a skill

```powershell
devagent install-skill   # copies skills/decompose-execute into ~/.claude/skills (Claude Code)
devagent install-hook    # optional: PreToolUse hook that enforces local-first routing
```

Once installed, the host model decomposes a substantial task, hands each piece to the local
executor, runs the gate, and reports the savings. See the skill body for the exact contract.

## Prerequisites

- **Local model**: a llama.cpp server with an OpenAI-compatible API at
  `http://localhost:8080/v1` (configurable), serving e.g. Qwen3 27B.
- **Frontier model**: the `claude` CLI installed and logged in (`claude auth status`). No API
  key needed. (Or set `ANTHROPIC_API_KEY` to use metered API instead — see config.)

## Interactive shell

Run `devagent` with no command to drop into a resident session — like the `claude` or `codex`
CLIs — instead of spawning a new process per action:

```text
$ devagent
devagent 0.1.0 — interactive shell
repo: C:\Users\ADMIN\devagent
type a task to run it · /ask <q> to ask · /help · /exit

devagent (devagent)> add a /health endpoint that returns build info
…                                    # runs the full pipeline, with confirmations
[dry] devagent (devagent)> /ask what does the router do?
The router (devagent/models/router.py) resolves a role to a model chain …
devagent (devagent)> /epic plan "migrate billing to the outbox pattern"
```

- **plain text** → a coding task (decompose → execute → gate → apply), confirmations included
- **`/ask <question>`** → read-only Q&A about the repo via the local model (never edits)
- **`/repo <path>`**, **`/dry` `/auto` `/review` `/test` `/parallel`** (toggle run flags),
  **`/clear`**, **`/help`**, **`/exit`** (or Ctrl-D); Ctrl-C aborts the current task
- **any other `/command`** passes straight through to the CLI below (`/cost`, `/trace`,
  `/epic …`, `/undo`, …)

The one-shot commands below all still work unchanged from a normal shell.

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

devagent plan "<task>"               # decomposition-first: show the subtask plan (no execution)
devagent plan-import --file plan.json --strict   # ingest a host-authored plan (validated)
devagent run --from-plan <id>        # execute a saved/reviewed plan verbatim (no re-decomposition)
devagent verify                      # non-destructive integration gate: interfaces + impacted tests
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

# Skill & enforcement
devagent install-skill               # install the decompose-execute skill into ~/.claude/skills
devagent install-hook                # install the PreToolUse local-first enforcement hook
devagent enforce on|off|status       # toggle local-first enforcement for a repo

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
devagent search "<query>"            # retrieval ranking (exact + BM25 + graph [+ semantic])
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
→ decompose → **blast radius** (file + service, incl. **cross-service HTTP/queue edges**) →
**incident lessons** → **write-locks** → **parallel waves** → per subtask: **specialized**
guidance → execute (ADR + pattern + incident + **shared-interface** context) → **safety rules +
compliance + migration gate + pattern enforcement** → gate → escalate → **reviewer** → apply →
**conformance** → **test runner** (auto-rollback) → ledger. A per-session **token/cost budget**
can hard-stop it.

## How a run works

```
index (free, local)
  → retrieve (~3 KB exact context; large files windowed; --file to target)
  → decompose:  in-envelope?  → DIRECT (local only, ~$0)
                otherwise      → frontier model splits into small subtasks (each declares
                                 the interface it `provides`, injected into dependents)
  → per subtask: local execute → deterministic gate (syntax/types/lint/security/tests)
                 gate fails?   → escalate (frontier returns corrected guidance) → re-execute
  → diff → keep / rollback → ledger (cost + quality)
  → devagent verify: cross-file interfaces resolve + impacted tests pass (integration gate)
```

Everything is snapshotted; sessions checkpoint per subtask so a crash can `resume`. Escalation
is triggered by a deterministic **gate failure**, never by a model's self-reported confidence.

## Continuity at scale (so a 100k-LOC codebase doesn't break)

- **Interface contracts** — each subtask declares what it `provides`; those exact signatures are
  injected into every dependent's prompt, and `devagent verify` statically flags any cross-file
  import that doesn't resolve. Independently-built pieces fit together.
- **Cross-service blast radius** — impact analysis follows not just Python imports but
  **HTTP routes and pub/sub topics**, so changing a service that serves `/x` pulls in the
  callers/tests of `/x` even with no import between them.
- **Impact-scoped tests** — `verify` / `--test` run the tests covering the change's blast
  radius (whole suite as fallback), as a fast integration gate.
- **Semantic retrieval (opt-in)** — configure an `embedder` role and the ranker blends embedding
  cosine in, surfacing the right file even when it shares no keywords with the task. Vectors are
  cached at index time, so it scales. Absent → lexical-only, fully offline.

## Cost & quality, measured

- **`devagent cost`** — actual vs counterfactual (same pipeline, frontier executor). With the
  CLI subscription, marginal cost is `$0` and the CLI's reported `total_cost_usd` becomes the
  **API billing avoided**.
- **`devagent quality`** — objective gate pass rate (the floor) + in-envelope rate + a sampled
  **differential parity rate** (`--audit` / `devagent audit`): the same task on the frontier
  model, compared by a blinded judge. The judge is a *signal*; the gate is the floor.

## Config

`~/.devagent/config.toml` — declare any number of models (three protocols: `openai-compat`,
`anthropic`, `cli`), route by **role** (executor/planner/reviewer/embedder/…), set fallback
chains, and tune the **parity envelope** (`max_context_tokens`, `max_file_lines`,
`max_subtask_files`).

## Development

```powershell
python -m pip install -e ".[validate]"
python -m pytest        # offline test suite (no network, no model calls)
ruff check devagent
```

See `SPEC.md` for the full design, the V1→V5 roadmap, and the reasoning.
