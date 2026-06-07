# devagent — Onboarding & Handover

A cost-efficient, multi-model coding CLI. A **local model** (Qwen via llama.cpp) does the work
*inside its parity envelope* (small, well-scoped tasks where it matches a frontier model). The
system keeps every task inside that envelope, verifies every output with a **deterministic
gate**, and consults a **frontier model** only to *decompose* hard tasks or fix a gate failure.

Frontier work runs through the **`claude` CLI subscription** (headless `claude -p`) — **no
per-token API billing**. Savings and quality are both measured.

> The bet: a 27B local model won't match a frontier model on a huge file/repo, but it's at
> parity on small scoped changes. So never hand it a big problem — decompose, retrieve precisely,
> window large files, gate everything, escalate rarely.

---

## Status

- **V1**, **V1.5**, **V2**, **V3**: COMPLETE — 135 offline tests, ruff-clean, CI matrix
  (ubuntu/windows × py3.11/3.12). Verified end-to-end on the claude subscription. No external
  binaries required.
- **V4 (semantic RAG, write-time pattern enforcement, compliance)**: next.
- Repo: local git at `C:\Users\ADMIN\devagent` on `main`. **No remote yet** — to publish:
  `git remote add origin <url> && git push -u origin main`.

## Quick start (PowerShell)

```powershell
cd C:\Users\ADMIN\devagent
python -m pip install -e ".[validate]"   # core + gate tools (mypy/ruff/bandit/pytest)
devagent init                            # ~/.devagent/config.toml
devagent status                          # models, gate tools, git
python -m pytest                         # 79 tests, no network/model calls
```

Prereqs: a llama.cpp OpenAI-compatible server at `http://localhost:8080/v1` (local executor);
the `claude` CLI logged in (`claude auth status`) for frontier roles. No API key required.

## Run flow

```
index → route (classifier) → contract-first (API tasks) → decompose
  → blast radius → per subtask: execute (ADR+pattern context) → safety rules
  → gate (syntax/types/lint/security/tests) → escalate-on-failure → apply
  → conformance → ledger (cost + quality)
```
Everything is snapshotted; sessions checkpoint per subtask so a crash can `devagent resume`.

## Architecture (where things live)

```
devagent/
├── cli.py            # Typer CLI (all commands)
├── pipeline.py       # the conductor — wires the whole run flow
├── config.py         # ~/.devagent/config.toml (models, roles, envelope, limits, pricing)
├── models/           # base, openai_compat, anthropic_client, cli_client, registry, router
├── context/          # index, retrieve, window, compress  (the parity-envelope machinery)
├── orchestration/    # classifier (direct vs plan_execute)
├── decompose/        # planner (split hard tasks into in-envelope subtasks)
├── execute/          # executor, edits (search/replace), apply (snapshot/diff/undo),
│                     #   escalate, contract (OpenAPI-first + conformance)
├── validate/         # gate (deterministic checks), safety_rules
├── knowledge/        # adr, pattern_registry, service_registry
├── planning/         # blast_radius
├── prove/            # audit (differential), calibrate (parity envelope mapping)
├── ledger.py         # SQLite: tasks + audits
└── report.py         # cost savings + quality
```

Per-repo config lives under `.devagent/`: `rules.yaml`, `adrs/`, `registry/services/`,
`patterns.yaml`, `contracts/`, plus runtime `sessions/`, `snapshots/`.

## Key concepts

- **Roles, not models.** Code asks the router for a *role* (executor/planner/reviewer/
  classifier/compressor); config maps each role to an ordered model chain with fallback. Three
  protocols: `openai-compat`, `anthropic`, `cli`.
- **Quality = the gate, not the model.** Escalation triggers on a deterministic **gate failure**,
  never on self-reported confidence.
- **Cost.** `cli` tier = $0 marginal; its reported `total_cost_usd` is the **counterfactual**
  (API billing avoided). `devagent cost` / `devagent quality` report the measured numbers.

## Commands

`run` (flags: `--file --executor --planner --dry-run --yes --audit --flag --contract`),
`cost`, `quality`, `audit`, `calibrate`, `log`, `undo`, `resume`, `status`, `init`,
`rules`, `services`/`service`, `adr` (list/show/new/check), `pattern` (list/add/deprecate),
`contract`.

## Conventions

- Commits: Conventional Commits, one coherent change each. Sign-off trailer:
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.
- Every change: `ruff check devagent` clean + `python -m pytest` green before committing.
- Tests are **offline** — no network, no model calls (use fakes / fixtures in `tests/conftest.py`).
- Windows: UTF-8 is forced in `cli.py`; subprocess calls avoid `shell=True`.

## Roadmap (SPEC.md is the source of truth)

- **V2** (DONE): cross-service dependency graph, OpenAPI breaking-change diff (pure Python — no
  `oasdiff`/`buf`), service-level blast radius, write locks, session token/cost budget,
  repo-graph caching, `services --check` contract-validation pipeline.
- **V3** (DONE): parallel execution (file-disjoint waves on the write-lock foundation), reviewer
  agent, test runner with auto-rollback, specialized agents by domain, test generator.
- **V4** (next): semantic RAG, pattern enforcement at write time, compliance rule sets.
- **V5**: autonomous long-horizon (epic decomposition, org-workflow integration).

See `SPEC.md` for the full design and rationale, and `README.md` for user-facing usage.
