# devagent — Handover & Status

_Last updated: 2026-06-07._ A cost-efficient, multi-model coding CLI. A **local model** (Qwen
via llama.cpp) does the work inside its **parity envelope** (small, well-scoped tasks); a
**frontier model** (the **`claude` CLI subscription** — no API billing) only decomposes hard
tasks or fixes a gate failure. Everything is verified by a deterministic gate; savings and
quality are measured.

- **Repo:** local git at `C:\Users\ADMIN\devagent`, branch `main`. **No remote yet.**
- **Size:** 62 package modules · 36 test files · **201 offline tests** (ruff-clean,
  CI matrix ubuntu/windows × py3.11/3.12).
- **Status:** **V1, V1.5, V2, V3, V4, V5 COMPLETE.**
- Verified end-to-end on the claude subscription, including a **self-improvement run** on this
  repo (the reviewer caught a real issue; tests passed; change committed).
- Shareable onboarding: https://claude.ai/claude-code/onboard/OugmukGx4Q8V

---

## Status at a glance

| Version | Theme | Status |
|---|---|---|
| V1 | Kernel: route → execute → gate → escalate → apply → ledger | ✅ Done |
| V1.5 | Knowledge & routing: ADRs, patterns, classifier, contract-first, safety rules, blast radius, service registry | ✅ Done |
| V2 | Multi-service: dep graph, OpenAPI breaking-change diff, service blast radius, write-locks, budget, index cache | ✅ Done |
| V3 | Parallel + review: wave scheduler, reviewer agent, test-runner+rollback, specialized agents, test generator | ✅ Done |
| V4 | Institutional knowledge + compliance: 3-tier retrieval, compliance profiles, migration gate, pattern enforcement, ADR lifecycle, incidents | ✅ Done |
| V5 | Autonomous long-horizon: epic decomposition, checkpointed multi-day task graphs, predictive conflict detection, cross-team reservations, autonomous arch proposals (approval-gated), decision-trail trace, org-workflow integration | ✅ Done |

---

## What's done (by area)

**Models & orchestration**
- 3 provider protocols: `openai-compat` (llama.cpp/GPT/…), `anthropic` (API), **`cli`** (spawns
  `claude -p` on the subscription — $0 marginal; captures the API-equivalent cost as the
  counterfactual). Route by **role** with fallback chains; thread-safe result metadata.
- Routing classifier (deterministic weighted matrix) → direct vs plan→execute.

**Parity-envelope machinery (context)**
- AST index (fingerprint-**cached**), **three-tier retrieval** (exact + BM25 + import-graph),
  large-file **windowing**, deterministic-first **compression**.

**Decomposition & execution**
- Frontier-model decomposition into in-envelope subtasks; **parallel** file-disjoint waves;
  specialized domain agents (migration/infra/frontend/api); search/replace edit application with
  file-scoped **snapshots**, diff preview, **undo**, per-subtask **resume**.

**Validation (the quality floor)**
- Deterministic gate: syntax → mypy → ruff → **bandit (security)** → tests.
- **Safety-rules engine** + **compliance profiles** (PCI/SOC2/HIPAA) + **DB migration gate** +
  **write-time pattern enforcement** — all evaluated before any write.
- **Reviewer agent** (HIGH finding rolls back) · **post-apply test runner** (auto-rollback) ·
  **contract conformance** diff-back.

**Knowledge layer**
- ADRs (semantic enforcement + full lifecycle), pattern registry (confidence decay + write-time
  enforcement), service registry + cross-service dependency graph, incident knowledge.

**Autonomous long-horizon (V5)**
- **Epic decomposition** (`longhorizon/epic.py`): frontier planner turns a goal into an
  epic→story→task tree with pre/postconditions; immutable plan in `.devagent/epics/<id>/epic.yaml`.
- **Checkpointed task-graph runner** (`longhorizon/runner.py`): dependency-ordered ready-task
  frontier, status rolls up to stories/epic, state checkpointed to `state.json` after every change
  — survives crashes, resumes across days/sessions. Execution is injected (CLI wires `pipeline.run`).
- **Predictive conflict detection** (`longhorizon/conflict.py`): before execution, flags direct
  file clashes (block), import-coupling clashes via the blast-radius graph (warn), and tasks
  touching files under another team's reservation (block).
- **Cross-team reservation system** (`longhorizon/reservation.py`): TTL'd, owner-keyed reservations
  on typed resources (`service:` / `table:` / `file:`) under `.devagent/reservations/`.
- **Autonomous architectural proposals** (`longhorizon/proposal.py`): frontier proposes one ADR;
  it lands `proposed` and waits at a human gate — `devagent propose --approve` promotes it into an
  enforced ADR.
- **Decision-trail observability** (`observability/trace.py`, closes gap #10): every run records
  routing inputs/verdict, assembled context, rules fired, blast radius, and per-subtask
  cost/time/model/status to `.devagent/traces/<session>.json`; `devagent trace` renders it.
- **Org-workflow integration** (`integrations/`): provider-agnostic (`null` default → offline
  outbox; `github` via `gh`, `jira` via REST, `slack` via webhook). `devagent epic sync` opens one
  issue per epic/story, idempotently.

**Reporting & safety**
- SQLite ledger; `cost` (API billing avoided) and `quality` (gate pass + in-envelope + audited
  parity); `audit` / `calibrate` (differential parity vs frontier); per-session token/cost
  budget; write-locks.

**Gaps closed from the original review:** #1 (escalate on gate failure, not confidence), #2
(durable resume), #4 (semantic ADR check), #6 (contract conformance), #7 (security in the gate),
#9 (parallel file-claims + consistency check), #10 (decision-trail `devagent trace`), #11 (pattern
decay), #13 (deterministic-first compression).

---

## What's pending

**Before "production" (highest priority)**
1. **Live local executor run** against a real llama.cpp server — to date the executor has been
   validated via the `claude` CLI; the `:8080` endpoint seen during testing didn't behave as an
   OpenAI server. Point `[models.qwen-local].base_url` at a real server and run.
2. **20-task acceptance benchmark** — the V1 ship criterion (≥95% tokens via Qwen AND ≥95%
   audited parity). The harness exists (`calibrate`/`audit`); it needs a real run.
3. **Git remote** — repo is local-only. `git remote add origin <url> && git push -u origin main`.

**Known limitations / partial items (from the original 15-gap review)**
- **#3 blast radius** is import-graph (intra-repo) + registry-level cross-service; it does **not**
  yet trace event/HTTP/shared-DB edges from code.
- **#5 clarification** — the classifier *scores* ambiguity but does **not** interactively ask
  clarifying questions before acting.
- **#8 service registry drift** — registries are hand-maintained YAML; **no auto-sync** from code
  (OpenAPI specs, client calls, manifests).
- **#9 semantic conflicts** — parallel waves enforce file-disjointness + a deterministic
  consistency check, but there is **no model-based cross-file semantic conflict check**.
- **#12 multi-repo** — single-repo only; **no workspace** concept spanning repos.
- **#14 data migrations** — no schema-version/migration for `~/.devagent/tasks.db` or the
  `.devagent/` formats across upgrades.
- **#15 long-running ops** — no async/background handling for hours-long migrations/backfills.
- **RAG embedding tier** — retrieval is lexical (BM25) + graph; the **vector/embedding tier is a
  pluggable slot**, not implemented (avoids a heavy model dependency).

**V5 (done) — live-integration follow-ups:** the org-workflow providers are built and wired with
an offline `null` default; the `github`/`jira`/`slack` backends are exercised only via fakes (no
remote, no `gh`, no creds in this environment). Point a provider at real credentials to verify
live. `devagent epic run` wires the runner to `pipeline.run`, so a full multi-day epic run depends
on the same live-executor prerequisite as the rest of the system.

---

## Run it (PowerShell)

```powershell
cd C:\Users\ADMIN\devagent
python -m pip install -e ".[validate]"   # core + gate tools
devagent init                            # ~/.devagent/config.toml
devagent status                          # models, gate tools, git
python -m pytest                         # 163 tests, no network/model calls
```
Prereqs: a llama.cpp OpenAI-compatible server at `http://localhost:8080/v1` (local executor);
`claude` CLI logged in (`claude auth status`) for frontier roles. No API key required.

Full command list, architecture map, and conventions: see **ONBOARDING.md**. Full design and the
V1→V5 roadmap: see **SPEC.md**.

---

## Conventions (for contributors)
- Conventional Commits, one coherent change each; trailer
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.
- Every change: `ruff check devagent` clean + `python -m pytest` green before committing.
- Tests are **offline** (no network/model calls) — use fakes/fixtures in `tests/conftest.py`.
- Windows: UTF-8 forced in `cli.py`; subprocess calls avoid `shell=True`.
