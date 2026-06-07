---
name: decompose-execute
description: Use when a coding task is substantial — a new feature, a change spanning multiple files, a refactor, or work in a large/unfamiliar repo where you'd otherwise write a lot of code yourself. You plan the work; a cheap LOCAL model (via the `devagent` CLI) writes each small piece and a deterministic gate verifies it, so the bulk of code generation runs at ~$0. Do NOT use for one-line/trivial edits, config tweaks, or answering questions — just do those directly.
---

# Decompose-Execute — plan with the strong model, build with the local one

On an in-scope task your job is to **decompose, orchestrate, and verify — not to write the
implementation yourself.** A cheap local model writes each subtask's code via `devagent`; a
deterministic gate (types → lint → security → tests) checks each one. You do the small, high-value
planning; the local model does the token-heavy generation at ~$0.

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
     there will be NO real cost savings.* Give the fix (`ollama serve` + `ollama pull
     qwen2.5-coder:7b`; set `base_url=http://localhost:11434/v1`, `model_id=qwen2.5-coder:7b`) and
     ask whether to wait or proceed anyway.
3. Ensure the working tree is **clean (committed)** first, so a partial run is easy to review/undo.
   Make sure `.devagent/sessions/`, `snapshots/`, `locks/`, `cache/`, `traces/`, `cli_io/` are
   gitignored (runtime artifacts); knowledge dirs (`adrs/`, `patterns.yaml`, `rules.yaml`) may be
   versioned.

## Step 1 — Understand, then clarify (don't plan blind)
Briefly explore the relevant code first (read the files the task touches, or `devagent plan` to see
how it decomposes). If the task is **ambiguous or under-specified, ask the user before planning** —
a plan built on a guess wastes a whole run.

## Step 2 — Decompose (you do this)
Break the task into the smallest safe, ordered subtasks. Each subtask MUST:
- touch **≤ 3 files**, be **one coherent change**, with a clear, testable outcome;
- list `target_files` and `depends_on` (ids that must finish first);
- **carry the interface it shares with other subtasks in its description.** If `s1` defines
  `OrderRepo.create(order) -> Order`, then `s2`'s description must state that exact signature so the
  local model calls it correctly. This is how independently-executed pieces stay consistent.
- be flagged if **inherently complex** (intricate logic/algorithm), even if small — see Step 5.

Write them as JSON, e.g. `plan.json`:
```json
[
  {"id": "s1", "description": "Add OrderRepo with create(order: Order) -> Order in app/orders/repo.py",
   "target_files": ["app/orders/repo.py"], "depends_on": []},
  {"id": "s2", "description": "Wire POST /orders to OrderRepo.create(order)->Order (see s1 signature)",
   "target_files": ["app/orders/api.py"], "depends_on": ["s1"]}
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

## Step 5 — Handle a subtask the local model can't do
If a subtask still fails the gate after the automatic escalation, do **one** of:
- **split it** into smaller pieces and re-import, or
- **route just that piece to the frontier**: `devagent run "<that subtask>" -f <files>
  --executor claude-cli --yes -p <repo>` (keeps the rest on local).

Do **not** silently take over and hand-write it — surface the gate output to the user first.

## Step 6 — Verify the goal (not just the gate)
The gate proves each piece compiles/passes; it does **not** prove the *task* is done. After the run,
confirm the original goal is actually achieved (run the feature/tests, check the acceptance the user
asked for). If it's half-built or wrong, that's a new subtask — not "done."

## Step 7 — Report savings (MANDATORY, and honestly)
Report:
1. the per-run line — **`saved $X (Y% local)`** — and cumulative `devagent cost -p <repo>`.
2. **The honest caveat:** that figure counts only work routed through `devagent` (the local
   execution vs. an all-frontier counterfactual). **Your own planning/orchestration tokens as the
   host are real and are NOT included**, and the savings are only meaningful if `Y% local` is high.
   If `Y% local` is 0, say plainly that nothing ran locally (local model down) → no savings.

## Hard rules
- **NEVER** hand-write implementation for an in-scope task — route it through `devagent`.
- **ALWAYS** report savings + `% local`, with the caveat above. Don't claim "100% local" for the
  whole task — it's execution-only.
- **VERIFY the goal**, not just the gate, before calling it done.
- If the gate keeps failing, surface it — split or escalate that piece; don't quietly take over.
