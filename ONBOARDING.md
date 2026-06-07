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

- **V1**, **V1.5**, **V2**, **V3**, **V4**, **V5**: COMPLETE — 201 offline tests, ruff-clean, CI matrix
  (ubuntu/windows × py3.11/3.12). Verified end-to-end on the claude subscription (incl. a
  self-improvement run on this repo). No external binaries required.
- **V5 (autonomous long-horizon)**: next.
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
├── knowledge/        # adr, pattern_registry, service_registry, compliance, incidents
├── planning/         # blast_radius, scheduler (parallel waves)
├── longhorizon/      # V5: epic (decomposition), runner (checkpointed graph), conflict,
│                     #   reservation (cross-team), proposal (approval-gated ADRs)
├── observability/    # V5: trace (decision trail → devagent trace)
├── integrations/     # V5: org-workflow providers (null/github/jira/slack) + epic sync
├── prove/            # audit (differential), calibrate (parity envelope mapping)
├── ledger.py         # SQLite: tasks + audits
└── report.py         # cost savings + quality
```

Per-repo config lives under `.devagent/`: `rules.yaml`, `adrs/`, `registry/services/`,
`patterns.yaml`, `contracts/`, `incidents/`, plus V5 `epics/<id>/` (epic.yaml + state.json +
sync.json), `reservations/`, `proposals/`, `traces/`, `integrations/outbox.jsonl`, and runtime
`sessions/`, `snapshots/`.

## Key concepts

- **Roles, not models.** Code asks the router for a *role* (executor/planner/reviewer/
  classifier/compressor); config maps each role to an ordered model chain with fallback. Three
  protocols: `openai-compat`, `anthropic`, `cli`.
- **Quality = the gate, not the model.** Escalation triggers on a deterministic **gate failure**,
  never on self-reported confidence.
- **Cost.** `cli` tier = $0 marginal; its reported `total_cost_usd` is the **counterfactual**
  (API billing avoided). `devagent cost` / `devagent quality` report the measured numbers.

## Commands

`run` (flags: `--file --executor --planner --dry-run --yes --audit --flag --contract --review
--test --parallel`), `cost`, `quality`, `audit`, `calibrate`, `log`, `undo`, `resume`, `status`,
`init`, `rules`, `services`/`service`, `adr` (list/show/new/set-status/check), `pattern`
(list/add/deprecate), `contract`, `contract-diff`, `gen-tests`, `search`, `compliance`,
`incidents`.
V5: `epic` (plan/list/show/conflicts/run/sync), `reserve`, `reservations`, `propose`
(`--list`/`--approve`/`--reject`), `trace`.

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
- **V4** (DONE): three-tier retrieval (exact+BM25+graph), compliance profiles (PCI/SOC2/HIPAA),
  DB migration gate, write-time pattern enforcement, full ADR lifecycle, incident knowledge.
- **V5** (DONE): autonomous long-horizon — epic decomposition (epic→story→task w/ pre/postconditions),
  checkpointed multi-day task-graph runner (resumable), predictive conflict detection, cross-team
  reservations, autonomous architectural proposals (human-gated → enforced ADR), decision-trail
  `devagent trace` (closes gap #10), provider-agnostic org-workflow integration (null/github/jira/slack).

See `SPEC.md` for the full design and rationale, and `README.md` for user-facing usage.
