---
name: decompose-execute
description: Use for ANY non-trivial coding task — a new feature, a multi-file change, a refactor, or work in a large/unfamiliar repo. You PLAN the work, then route EXECUTION to a cheap local model via the `devagent` CLI, which gates every change. The expensive model plans; the free local model writes the code; savings are always reported. Do NOT use for trivial one-line edits or for answering questions.
---

# Decompose-Execute — plan with the strong model, build with the local model

Your job on an in-scope task is to **decompose and orchestrate, not to write the implementation
yourself**. A cheap local model (via `devagent`) writes the code for each small piece, and a
deterministic gate verifies it. You do the planning (small, high-value); the local model does the
token-heavy generation (≈ $0). This is what produces the cost savings — and you MUST report them.

## When this applies (MANDATORY)
Apply this workflow whenever the task is more than a trivial one-liner: new features, multi-file
changes, refactors, or anything in a large repo. For such tasks you **MUST follow the steps below
and MUST NOT hand-write the implementation yourself.** Skip the skill only for: a literal
one-line/trivial edit, or a pure question (no code change).

## Step 0 — Preconditions (check first, MANDATORY)
Run:
```
devagent status -p <repo>
```
- If `devagent` is not installed → tell the user to `pip install -e .` in the devagent repo, stop.
- Read the **execution** section:
  - **local executor reachable** → good, proceed; execution will run locally (~$0).
  - **unreachable** → tell the user plainly: *"execution will fall back to the frontier model, so
    there will be NO cost savings."* Give them the fix (`ollama serve` + `ollama pull
    qwen2.5-coder:7b`, then set `base_url=http://localhost:11434/v1`, `model_id=qwen2.5-coder:7b`
    in `~/.devagent/config.toml`), and ask whether to wait or proceed anyway.

## Step 1 — Decompose (you do this)
Break the task into the smallest safe, ordered subtasks. Each subtask MUST:
- touch **≤ 3 files**, be **one coherent change**, have a clear, testable outcome,
- list its `target_files` and its `depends_on` (ids of subtasks that must finish first),
- be small enough for a 7–14B local model to implement from a tiny slice of context.

Write them as JSON, e.g. `plan.json`:
```json
[
  {"id": "s1", "description": "add the OrderRepository with a create() method",
   "target_files": ["app/orders/repo.py"], "depends_on": []},
  {"id": "s2", "description": "wire POST /orders to OrderRepository.create",
   "target_files": ["app/orders/api.py"], "depends_on": ["s1"]}
]
```

## Step 2 — Save the plan
```
devagent plan-import --task "<the overall task>" --file plan.json -p <repo>
```
This prints a **plan id**. Show the user the subtask plan and let them confirm or adjust (they can
edit `.devagent/plans/<id>.yaml` directly — reorder, split, fix target_files/depends_on).

## Step 3 — Execute on the local model (do NOT write the code yourself)
```
devagent run --from-plan <id> -p <repo>
```
The local model implements each subtask; the deterministic gate (types → lint → security → tests)
verifies each; gate failures escalate automatically. You orchestrate and review — you do not type
the implementation.

## Step 4 — Show the savings (MANDATORY)
After the run, report to the user:
1. the per-run line from the summary — **`saved $X (Y% local)`**, and
2. cumulative savings — run `devagent cost -p <repo>` and show the table.

If **Y% local is 0**, state plainly that nothing ran on the local model (it was down), so there
were no savings this run, and point back to Step 0.

## Hard rules
- **NEVER** hand-write implementation code for an in-scope task — route it through `devagent run`.
- **ALWAYS** report the cost saved (and % local) after a run.
- If the gate keeps failing after escalation, surface the gate output to the user — do not quietly
  take over and write it yourself.
- Keep each subtask inside the local model's envelope (small + scoped); if a subtask is too big,
  split it further before executing.
