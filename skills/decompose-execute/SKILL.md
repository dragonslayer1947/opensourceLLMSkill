---
name: decompose-execute
description: Use when a coding task is substantial — a new feature, a change spanning multiple files, a refactor, or work in a large/unfamiliar repo where you'd otherwise write a lot of code yourself. You plan the work; a cheap LOCAL model (via the `devagent` CLI) writes each small piece and a deterministic gate verifies it, so the bulk of code generation runs at ~$0. Do NOT use for one-line/trivial edits, config tweaks, or answering questions — just do those directly.
---

# Decompose-Execute — plan with the strong model, build with the local one

On an in-scope task your job is to **decompose, orchestrate, and verify — not to write the
implementation yourself.** A capable local model writes each subtask's code via `devagent`; a
deterministic gate (types → lint → security → tests) checks each one. You do the small, high-value
planning; the local model does the token-heavy generation at ~$0.

## Hard decomposition is MANDATORY — and the local model is strong enough
Every in-scope task **MUST** be decomposed before any code is written. There is no "this one is
simpler, I'll just do it myself." Decompose **hard**: keep splitting until each subtask is small
and unambiguous enough to implement correctly in a single shot.

**Trust the executor.** The local model is **Qwen3.6 27B with a 128K-context window** — a strong
coding model that is at parity with frontier models on well-scoped changes, and whose large
context window lets it take a generous, precise slice of the repo per subtask. A properly
decomposed subtask is well within its ability.

So when a subtask fails, the correct conclusion is **"the subtask was too big or too vague" →
split it further** — NOT "the model is too weak, I'll take over." Your instinct that *"I could just
write this faster/better myself"* is exactly the impulse this skill exists to override: convert that
energy into a sharper decomposition, and let Qwen3.6 27B build it. Frontier execution of a piece is
a last resort (Step 5), not the reflex.

**You guarantee quality by VERIFYING, not by authoring.** You don't have to trust the local output
blind — you check it: read the diff, confirm the gate passed (add `--review` for a model review of
each diff), and confirm the goal is met (Step 6). That is your real job here — be the reviewer, not
the writer. If a diff is wrong, the fix is to **re-decompose or re-run that piece**, never to
hand-edit it yourself. Channel "I want this to be good" into review + sharper splitting.

## When to use vs. skip (be honest about the trade-off)
**Use it** when the task would otherwise have you writing substantial code: a feature, a
multi-file change, a refactor, or anything in a big repo. **Skip it** (just do it directly) for: a
single-line or trivial edit, a config/doc tweak, or a question — the plan→import→execute→gate loop
isn't worth the overhead there. If another planning skill is already driving the task, defer to it.

## Step 0 — Preconditions (MANDATORY, check first)
1. `devagent status -p <repo>` — `<repo>` is the project root you're working in (the dir holding
   its VCS/build files), not necessarily the cwd. Confirm `devagent` is installed.
2. Read the **execution** section:
   - **local executor reachable** → proceed; execution runs locally (~$0).
   - **unreachable** → tell the user plainly: *execution will fall back to the frontier model, so
     there will be NO real cost savings.* Give the fix (start the local server — e.g. llama.cpp
     serving **Qwen3.6 27B** at `http://localhost:8080/v1`, or Ollama — and point
     `[models.qwen-local]` at it) and ask whether to wait or proceed anyway.
3. Ensure the working tree is **clean (committed)** first, so a partial run is easy to review/undo.
   Make sure `.devagent/sessions/`, `snapshots/`, `locks/`, `cache/`, `traces/`, `cli_io/` are
   gitignored (runtime artifacts); knowledge dirs (`adrs/`, `patterns.yaml`, `rules.yaml`) may be
   versioned.

## Step 1 — Understand, then clarify (don't plan blind)
Briefly explore the relevant code first (read the files the task touches, or `devagent plan` to see
how it decomposes). If the task is **ambiguous or under-specified, ask the user before planning** —
a plan built on a guess wastes a whole run.

## Step 2 — Decompose (you do this)
Break the task into the smallest safe, ordered subtasks — decompose hard (see the principle
above). Each subtask MUST:
- touch **≤ 3 files**, be **one coherent change**, with a clear, testable outcome that
  **Qwen3.6 27B (128K ctx) can implement from the retrieved slice** — if you doubt it can, the
  piece is still too big: split again;
- list `target_files` and `depends_on` (ids that must finish first);
- declare in **`provides`** the exact interface(s) the subtask exposes (names + signatures). These
  are injected verbatim into every dependent subtask's prompt, so independently-built pieces call
  each other correctly. This is the single most important field for avoiding drift — be precise
  (e.g. `"store: module-level InMemoryStore singleton (import: from app.store import store)"`).
- be flagged if **inherently complex** (intricate logic/algorithm), even if small — see Step 5.

Write them as JSON, e.g. `plan.json`:
```json
[
  {"id": "s1", "description": "Add OrderRepo with create(order: Order) -> Order in app/orders/repo.py",
   "target_files": ["app/orders/repo.py"], "depends_on": [],
   "provides": ["class OrderRepo with create(order: Order) -> Order in app/orders/repo.py"]},
  {"id": "s2", "description": "Wire POST /orders to OrderRepo.create",
   "target_files": ["app/orders/api.py"], "depends_on": ["s1"], "provides": []}
]
```

## Step 3 — Import the plan (validated)
```
devagent plan-import --task "<overall task>" --file plan.json -p <repo> --strict
```
`--strict` rejects a plan with dangling/cyclic deps, duplicate ids, over-envelope subtasks, or
out-of-repo paths. Fix and re-import if it complains. Show the user the plan; let them edit
`.devagent/plans/<id>.yaml` (reorder/split/fix) before running.

## Step 4 — Execute on the local model (do NOT write the code yourself)
```
devagent run --from-plan <id> -p <repo>
```
Add the engine's own safety levers when warranted:
- `--review` for risky/auth/payments/data-loss changes (reviewer agent; HIGH finding rolls back),
- `--test` to run the suite after applying (auto-rollback on failure),
- `--contract` is on by default for API tasks.

The local model implements each subtask; the gate verifies each; failures escalate automatically.

## Step 5 — When a subtask fails the gate: split first, escalate last
A gate failure is almost always a **decomposition problem, not a model problem** — Qwen3.6 27B can
implement well-scoped work. So, in order:
1. **Split it further** into smaller, sharper subtasks and re-import — this resolves the large
   majority of failures and keeps the work (and the savings) on the local model.
2. Only if a piece is **genuinely frontier-hard** (novel algorithm, subtle cross-cutting design)
   after splitting, route *that one piece* to the frontier:
   `devagent run "<that subtask>" -f <files> --executor claude-cli --yes -p <repo>`.

**NEVER** silently take over and hand-write it because the local model "isn't getting it" — split
and retry, and surface the gate output to the user. Taking over is the failure mode, not the fix.

## Step 6 — Verify the goal (MANDATORY: run the integration gate)
The per-file gate proves each piece lints/compiles; it does **not** prove the pieces fit together or
that the *task* is done. After the run you **MUST** run the non-destructive integration gate:
```
devagent verify -p <repo>
```
It checks that cross-file imports resolve (no interface drift) and runs the tests covering the
change. If it fails, **fix the offending piece by re-running it** (Step 5) — never hand-edit. Then
confirm the original goal is actually achieved (exercise the feature). If a public surface has no
test, add a subtask that writes one. Half-built or wrong = a new subtask, not "done."

## Step 7 — Report savings (MANDATORY, and honestly)
Report:
1. the per-run line — **`saved $X (Y% local execution)`** — and cumulative `devagent cost -p <repo>`.
2. **The honest caveat (now measurable):** that figure is **execution-only** — local execution vs.
   an all-frontier-execution counterfactual. **Your own planning/orchestration tokens as the host
   are real and are NOT part of the savings** (you'd spend them either way). To make the number
   honest end-to-end, pass your own token spend for the task:
   `devagent run … --host-in <in> --host-out <out>` — the engine then reports net end-to-end cost
   and a true **`% local end-to-end`**. If you can't estimate them, say so. The savings are only
   meaningful if `% local execution` is high; if it's 0, nothing ran locally (local model down) →
   no savings.

## Hard rules
- **ALWAYS decompose (hard)** an in-scope task — never skip planning because it "seems simple
  enough to just do." Split until each piece fits Qwen3.6 27B.
- **NEVER** hand-write implementation for an in-scope task — route it through `devagent`.
- A failing subtask means **split further**, not take over — trust the 27B on well-scoped pieces.
- **ALWAYS** report savings + `% local`, with the caveat above. Don't claim "100% local" for the
  whole task — it's execution-only.
- **VERIFY the goal**, not just the gate, before calling it done.
- If the gate keeps failing, surface it — split or escalate that piece; don't quietly take over.
