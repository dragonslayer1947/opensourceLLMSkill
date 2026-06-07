# devagent — Software Engineering System

> Not a coding assistant. A system that maintains the integrity of a distributed
> sociotechnical system across teams, services, time, and scale.

Working name: `devagent`. Target: production super-apps at Amazon/Flipkart scale.

> **V1 + V1.5 status: BUILT.** The kernel and the knowledge/routing layer are implemented,
> installed, and tested (79 offline tests, ruff-clean, CI matrix). Frontier work runs through the
> `claude` CLI subscription (no API billing). Verified end-to-end including the V1.5 flow:
> retrieve → route (classifier) → contract-first (API tasks) → decompose → blast radius → execute
> (with ADR + pattern context) → safety rules → gate → escalate → apply → conformance → ledger,
> with cost-savings and quality (audit/calibrate) reporting.
>
> **V1.5 delivered:** safety-rules engine, intra-repo blast radius, service registry, ADR system
> with semantic enforcement (gap #4), pattern registry with confidence decay (gap #11), routing
> classifier, and contract-first with conformance diff-back (gap #6).
>
> **V2 (multi-service) status: BUILT.** Cross-service dependency graph + service-level blast
> radius; pure-Python OpenAPI breaking-change diff (`contract-diff`) and the cross-service
> contract-validation pipeline (`services --check`); file write-locks; per-session token/cost
> budget; fingerprint-keyed repo-index cache. 105 offline tests, ruff-clean. No external
> binaries required (oasdiff/buf replaced by the pure-Python diff).
>
> Remaining before "production": run the executor against a live local llama.cpp server, the
> 20-task acceptance benchmark, and V3 (parallel agents on the write-lock foundation, reviewer
> agent, test-runner with auto-rollback).

---

## Mental model shift

| Coding assistant | Software engineering system |
|---|---|
| Unit of work: file | Unit of work: invariant |
| Knows: file contents | Knows: system state + history + constraints |
| Question: "write this code" | Question: "what is the safest next action that preserves system integrity?" |
| Fails at: cross-service consistency | Handles: distributed sociotechnical systems |
| Scope: current session | Scope: multi-day, multi-team, multi-repo |

Every design decision in this system must serve this model.

---

## Cost principle

> Qwen3 27B (local, llama.cpp) = ~$0. Claude Opus = expensive.
> Target: 90%+ of all tokens through Qwen. Claude only where Qwen genuinely cannot.
> Every architectural decision must be evaluated against this constraint.

---

## Token economics + gate-driven quality

> The two hard requirements — *spend almost nothing* and *ship production-grade code on a
> local model* — are satisfied by the same lever: **precision**. Less context is cheaper
> AND more correct (less distraction → fewer retries). They are not a tradeoff.

**Quality comes from the gate, not the model.** A 27B local model cannot match Opus on raw
reasoning — and it doesn't need to. Production-grade output comes from a rigorous, deterministic
verification loop, not a brilliant generator. Mediocre generator + unforgiving gate + *rare*
expert escalation > brilliant generator with no gate. The model is allowed to be ordinary
because the *system* is rigorous.

**Where tokens actually go.** Input dwarfs output ~10:1. Input cost is dominated by (1) context
bloat — dumping "probably relevant" files, and (2) flailing — wrong output → retry → re-send
everything. "Use Qwen not Claude" is not the main lever. The main levers are: **send less, get
it right the first time, never re-send what's stable.**

**The 3-tier ladder — deterministic-first, local-second, cloud-last:**

```
Tier 0 — DETERMINISTIC TOOLS  ($0, no model)        ← try FIRST, always
  AST refactor/rename, type check, lint, test, format, symbol search,
  dependency graph, semgrep/bandit. The cheapest token is the one never spent.

Tier 1 — QWEN LOCAL  (~$0)                           ← ~99% of all TOKENS
  ALL context I/O: index, retrieve, rank, compress, generate, self-check.
  Burn local compute lavishly to be miserly with cloud tokens.

Tier 2 — CLAUDE  (expensive)                         ← rare oracle, surgical payloads
  Only irreducible reasoning. Sees 4–6K pre-digested tokens, outputs a short plan.
```

The ratio that matters is **99%+ of *tokens* on Qwen**, not 90% of tasks — because Qwen absorbs
the entire context surface and Claude only ever sees compressed single-shot prompts.

**Four choices that save the most:**
1. **Precise retrieval, never repo dumps** — assemble ~3 KB of exactly-right context, not 200 KB of maybe-relevant. Cheaper and more correct.
2. **Stateless single-shot calls** — no growing conversation. History re-sent every turn is the biggest hidden tax. Each task = a fresh minimal prompt.
3. **Deterministic compression → stable cache prefix** — extract signatures/types/exceptions/interfaces verbatim, then summarize only remaining logic. Stops silent info loss AND keeps Claude's cached prefix byte-stable so cache actually hits.
4. **The gate is mandatory and free** — syntax → types → imports → tests → security → contract conformance, all ~$0, all run *before* any Claude escalation. This is what makes Qwen output production-safe and escalation *rare*.

**Escalate on deterministic failure, not on vibes.** Self-reported confidence is miscalibrated;
the validator failing is the trustworthy trigger.

**Measurement is the purpose of V1.** You cannot optimize what you don't measure. V1 must log
tokens-in/out per model per task and show a running cost ledger — every later layer is justified
by its measured effect on the Qwen:Claude token ratio.

---

## The parity envelope: quality preserved by construction

**Diagnosis (the real problem):** Qwen3 27B is at parity with a frontier model on *small,
well-scoped* tasks. It degrades on *large files* and *large repos* — i.e. as **context scale**
grows. The gap is not raw reasoning; it's effective long-context utilization (attention
dilution, lost-in-the-middle). A 27B model's *quality* window is far smaller than its
*advertised* token window.

**Consequence:** quality is a function of how much context Qwen must hold, not the task's
intrinsic difficulty. So the strategy is not "hope Qwen is good enough" — it is:

> **Shrink every task until it lands inside the regime where Qwen == frontier. Never let Qwen
> see a large file or a large repo. Make the working set small, always.**

Inside its parity envelope, Qwen produces frontier-quality output at local cost **by
construction**, not by luck.

**The envelope is measured, not assumed.** A calibration benchmark characterizes parity across
`(task-type × context-size × file-size)` by running tasks on both models and comparing. Routing
uses the *measured* map. If a task type lags even when small, that pocket routes to the frontier
model. We never bet the quality guarantee on an unverified belief.

**The two named problems → two techniques:**

| Problem | Technique | Effect |
|---|---|---|
| Large repo | Precise retrieval (Qwen indexes free; assemble ~3 KB exact context) | Repo size irrelevant — Qwen sees only the relevant slice |
| Large file | Skeleton + focus windowing | Qwen edits a small window + signature-map of the rest |
| Large task | Decomposition by the frontier model | Big task → many small in-envelope edits |

**The frontier model's real job is decomposition, not execution.** Decomposing a large task
into in-envelope pieces is (a) the highest-leverage use of a smart model, (b) cheap because the
output is small (a plan), and (c) the act that *creates* the regime where Qwen performs at
parity. That is why plan-first matters: the plan is the tool that shrinks the task into the envelope.

**The quality guarantee is a stack, not a single check:**
1. Decompose into the parity envelope (frontier, cheap output)
2. Precise retrieval + file windowing (keep context small)
3. Deterministic gate (types, tests, security, contract conformance) — objective floor
4. In-envelope check (predictive parity signal; if irreducible, route up)
5. Differential audit, sampled (measured proof parity holds; recalibrate on drift)
6. Escalation (anything that fails the gate or can't be reduced → frontier model)

---

## Reporting: cost savings + quality (both first-class)

Every task and session reports **what you saved** and **proof that quality held**.

### Cost savings
- **Actual cost** = real token spend (Qwen ≈ $0 + any cloud calls actually made).
- **Counterfactual cost** = the *same task through the same pipeline* with the executor swapped to the frontier model (same retrieval, windowing, plan). Isolates the honest local-vs-frontier execution-cost difference.
- **Savings** = counterfactual − actual, per task / session / cumulative.
- Counterfactual is a labeled *estimate* (frontier input ≈ assembled context tokens, output ≈ actual output × frontier price). Under-claim by default.

```
devagent cost
  This task:  $0.00 actual  vs  $0.41 frontier   →  saved $0.41
  Session:    $0.07 actual  vs  $12.80 frontier  →  saved $12.73  (99.5%)
  This month: $3.10 actual  vs  $640 frontier    →  saved $637
```

### Quality (proof, not promise)
- **Objective gate score (free, every task):** types ✓ · tests N/N ✓ (coverage Δ) · security ✓ · lint ✓ · contract conformance ✓. The hard floor.
- **Measured parity rate (sampled, QA budget):** differential audit runs the same task on the frontier model; a blinded judge compares. "Qwen judged equivalent-or-better on X% of audited tasks." Scope is tunable — on-demand, all high-stakes tasks, + a small random % — so QA cost is controlled. The cost of *proof*.
- **In-envelope indicator (free):** whether the task stayed inside the measured parity envelope.

```
devagent quality
  Objective gate:   100% pass (last 50 tasks)
  Measured parity:  96% equivalent-or-better vs frontier (audited 1 in 10)
  In-envelope:      48/50 tasks (2 escalated to frontier)
```

Honest caveat: the audit uses an LLM judge (position/verbosity bias) — it is a *signal*; the
deterministic gate is the *floor*.

---

## The four layers (the target architecture)

```
┌─────────────────────────────────────────────────────┐
│  KNOWLEDGE LAYER                                    │
│  What the system is                                 │
│  ADRs · Service registry · Pattern registry         │
│  Schema registry · Constraint registry · RAG index  │
└─────────────────────────────────────────────────────┘
           │ feeds
┌─────────────────────────────────────────────────────┐
│  PLANNING LAYER                                     │
│  What the system should become                      │
│  Task graph · Blast radius scoring                  │
│  Impact analysis · Parallel workstream coordinator  │
└─────────────────────────────────────────────────────┘
           │ drives
┌─────────────────────────────────────────────────────┐
│  EXECUTION LAYER                                    │
│  How to get there safely                            │
│  Specialized agents · Contract-first workflow       │
│  Atomic transactions · Human gates                  │
└─────────────────────────────────────────────────────┘
           │ verified by
┌─────────────────────────────────────────────────────┐
│  VALIDATION LAYER                                   │
│  Whether we got there correctly                     │
│  Contract conformance · Safety rules engine         │
│  Consistency oracle · Integration test harness      │
└─────────────────────────────────────────────────────┘
```

These four layers are the **target architecture**, not the V1 scope. V1 ships the
**Execution** and **Validation** layers in full (the gate is the whole thesis), with only a
hardcoded sliver of Knowledge and Planning. The Knowledge layer (ADRs, patterns, service
registry, routing classifier) arrives in V1.5 — added only after the kernel is built and its
token savings are *measured*.

---

## Version roadmap

### V1 — The kernel (prove the economics, measured)
> The thinnest vertical slice that proves the core bet: **a local model does ~all the work,
> a deterministic gate makes it production-safe, the cloud model is consulted rarely — and we
> can *measure* the token savings.** Everything else is roadmap until this works and is proven.

**The kernel — 7 pieces:**
1. **Model clients** — Qwen (llama.cpp) + Claude, swappable by config
2. **Context-scale control** — keep every task inside Qwen's parity envelope. Precise retrieval (assemble ~3 KB exact context, never dump the repo → attacks large-repo degradation) + skeleton+focus windowing (full target region + signature-map of the rest, never the whole file → attacks large-file degradation). *This is the single biggest lever — it's what makes local-model quality equal frontier quality.*
3. **Qwen executor** — stateless, single-shot. Each task = a fresh minimal prompt; no growing conversation.
4. **Deterministic verification gate** — mandatory, ~$0, runs before any escalation:
   syntax (tree-sitter) → types (mypy --strict) → imports → tests → **security (semgrep/bandit)**
5. **Escalate-to-Claude on gate failure** — not on self-reported confidence. Qwen compresses context (deterministic extraction first), Claude sees ~4–6K tokens and returns a corrected PLAN only; Qwen re-executes.
6. **Snapshot → diff → apply → undo** — git stash or file copy; atomic apply; `devagent undo` always works.
7. **Token/cost ledger** — tokens-in/out per model per task, running cost. *The purpose of V1 is to measure.*

**Kernel-critical invariants (baked in from day one):**
- Escalation trigger = deterministic validator failure, never the `<confidence>` tag (gap #1)
- Durable, resumable task state — never leave a half-written system; `devagent resume <id>` (gap #2)
- Security scan is part of the gate, not an afterthought (gap #7)

**V1 ship criteria:**
- [ ] `devagent run "<task>"` on a single Python repo: retrieve/window → Qwen generate → gate → apply
- [ ] Large-file task handled via skeleton+focus windowing — no whole-file dump
- [ ] Gate blocks any code that fails syntax / types / tests / security — no exceptions
- [ ] Claude is invoked *only* when the gate fails after Qwen retry, and sees ≤6K tokens
- [ ] `devagent cost` reports actual vs counterfactual → **savings** (per task + cumulative)
- [ ] `devagent quality` reports gate pass rate + sampled parity rate + in-envelope rate
- [ ] Parity calibration run: envelope thresholds (task-type × context-size × file-size) recorded
- [ ] Snapshot + `undo` + `resume` all work reliably across a crash
- [ ] **Measured on a 20-task benchmark: ≥95% of tokens via Qwen AND ≥95% parity rate on the audited subset**

**Out of scope for V1 (deferred, not dropped):**
- Knowledge layer: ADRs, pattern registry, service registry → **V1.5**
- Routing classifier, contract-first, blast radius, safety-rules engine → **V1.5**
- Multi-service, parallel agents, semantic RAG → V2+

---

### V1.5 — Knowledge & routing (added once savings are proven)
> Now that the kernel works and is measured, add the layers that reduce cost *further* and
> raise quality. Each item is justified by its measured effect on the Qwen:Claude token ratio.

**Additions:**
- ADR system: machine-readable YAML, constraints auto-extracted; **violation check is a Qwen call, not regex** (gap #4)
- Pattern registry: detect existing patterns + write back novel solutions; patterns have confidence decay + deprecation (gap #11)
- Routing classifier (Qwen, ~200 tokens): pick Direct vs Plan→Execute up front, so obvious work skips even the gate-retry loop
- Contract-first for API tasks: OpenAPI spec written first, **and implementation diffed back against the spec** to close the loop (gap #6)
- Safety-rules engine (`.devagent/rules.yaml`): block writes to auth/migrations/PCI zones without an explicit flag
- Blast radius report (intra-service to start): show impact before execution
- Service registry: one entry, schema designed for 50+

**V1.5 ship criteria:**
- [ ] Pattern hit → routes Direct to Qwen, no Claude, no wasted gate retries
- [ ] ADR violation caught semantically on a diff that regex would miss
- [ ] Contract conformance check blocks an implementation that diverges from its spec
- [ ] Measured: token ratio improved vs V1 baseline (patterns reduce escalations)

---

### V2 — Multi-service awareness
> Agent understands the distributed system, not just one repo.

**Additions:**
- Service registry populated (all services, dependencies, SLAs, owned APIs)
- Cross-service dependency graph: which service calls which, via which contract
- Contract validation pipeline: OpenAPI diff (`oasdiff`) + Protobuf breaking change (`buf breaking`) before every merge
- Blast radius scoring at service level: "this change breaks N downstream consumers"
- Write lock system: explicit lock on cross-cutting changes (auth, shared libs, DB schema)
- Planner agent (Claude): generates step-by-step plan before execution
- Router: simple tasks → Qwen direct; architectural tasks → plan then execute
- Repo graph: cached, incremental (invalidated on git diff)
- Token budget tracking and hard cap per session
- Human checkpoint: pause when blast radius score exceeds threshold

---

### V3 — Parallel execution + review loop
> Multiple agents, checked by a reviewer, protected by tests.

**Additions:**
- Parallel agents with file claim system (no two agents write same file)
- Specialized agents by domain: infra agent, API agent, frontend agent, migration agent
- Reviewer agent: reads generated diff, flags issues before apply
- Test runner: run existing tests post-apply, auto-rollback on failure
- Test generator: generate tests for new code (coverage-gated merge)
- Integration test harness: invoked after each service change
- Session resume: interrupted task resumes from last checkpoint
- Cost cap: hard token limit per session, configurable
- Workstream coordinator: manages parallel teams working on disjoint services

---

### V4 — Institutional knowledge + compliance
> The system remembers everything and enforces what matters.

**Additions:**
- Semantic RAG (three-tier retrieval):
  1. Exact match on service names / API paths
  2. Semantic search over code summaries + ADRs + runbooks
  3. Graph traversal over dependency graph
  Context assembled per-query, not loaded wholesale
- Pattern registry with enforcement: AST-matching at write time, not just documentation
- Full ADR lifecycle: draft → accepted → deprecated, with constraint auto-generation
- Constraint registry: compliance rule sets (SOC2, PCI-DSS profiles)
- Safety rules engine extended: policy-as-code DSL, rules stored in repo and versioned
- Incident knowledge integration: past incidents feed into blast radius scoring
- DB migration safety gate: dry-run + rollback plan + affected-service analysis required

---

### V5 — Autonomous long-horizon
> Build features that span weeks, teams, and services without losing coherence.

**Additions:**
- Full epic decomposition: epic → story → task → subtask, each with preconditions/postconditions
- Multi-day task graphs with checkpointing and resume
- Predictive conflict detection before execution starts
- Organizational workflow integration: Jira, GitHub PRs, Slack approvals as native primitives
- Cross-team coordination protocol: reservation system for shared resources
- Autonomous architectural proposals with human approval gates
- Full observability: per-task cost, time, blast radius, test coverage delta

---

## Orchestration layer

This is the engine between planning and execution. It decides who does what, at what cost.

### Model responsibility split

| Task type | Model | Reason |
|---|---|---|
| CRUD endpoint with existing pattern | Qwen | Pattern exists, low ambiguity |
| Boilerplate: Kafka consumer, gRPC stub, migration | Qwen | Template-driven |
| Test generation given typed interface | Qwen | Deterministic from signature |
| Config changes, imports, formatting | Qwen | Mechanical |
| Implementation given a tight spec | Qwen | Spec removes ambiguity |
| Cross-cutting architectural decision | Claude Opus | Novel reasoning required |
| Decomposing ambiguous requirements | Claude Opus | Disambiguation requires depth |
| Failure mode analysis at scale | Claude Opus | "What breaks at 10M orders/day?" |
| Generating first ADR / API contract | Claude Opus | No prior pattern to follow |
| Security threat modeling (auth, payments) | Claude Opus | High-stakes, needs depth |
| Reviewing Qwen output that hit escalation | Claude Opus | Selective, compressed context only |

**Critical rule:** the cloud model never writes implementation. It produces PLAN.md, ADRs, and interface contracts only. Qwen executes. This keeps cloud calls short and cacheable.

---

### Default routing: plan-first vs escalate-on-failure

Two valid strategies at different task tiers — the classifier (V1.5) picks:

- **Escalate-on-failure** (try Qwen, escalate on gate failure): right for *trivial-to-moderate* tasks where Qwen usually succeeds. The gate catches correctness failures cheaply.
- **Plan-first** (strong model writes the plan up front, Qwen executes): right for *architecturally significant* tasks. Highest-ROI cloud spend available — the plan output is tiny (cheap even on a frontier model), and a good plan prevents the failure mode the gate **cannot** catch: **Qwen building the wrong thing correctly.** The gate verifies "compiles / passes / secure," never "is this the right design." Only a reasoning model closes that gap.

The V1 kernel ships only escalate-on-failure — not because it's always best, but because it's the minimal slice that *measures* what fraction of real tasks genuinely need the strong model. If the benchmark shows complex tasks dominate, plan-first becomes the V1.5 default — driven by data, not dogma.

---

### Model registry & fallback chains

The system is provider-agnostic. Declare any number of models in config; route by **role**, not hardcoded name.

- **Two protocols cover almost everything:** OpenAI-compatible (llama.cpp, GPT, OpenRouter, Together, most others) + Anthropic. You don't need 7 client implementations for 7 models — 2 protocols + N config entries.
- **Role → ordered model chain:** each role (classifier, executor, compressor, planner, reviewer) maps to a preference list. First = primary; rest = fallback chain.
- **Fallbacks are production-critical:** on timeout / rate-limit / 5xx / circuit-open, fall to the next model. Local model gets a timeout→cloud fallback (llama.cpp can hang or OOM).
- **Discipline:** build the abstraction for N now (cheap, future-proof); *operate* with the minimum the data justifies (start with 2). Add a model only when you measure a task class where it wins on cost-quality. Every model is also prompt-tuning + eval surface — it must earn its slot.

---

### The five orchestration patterns

```
DIRECT
  Task → RoutingClassifier (Qwen, ~200 tokens) → QwenExecutor → Validator → Apply
  When: CRUD, known template, single file, existing pattern
  Cost: ~$0

PLAN → EXECUTE
  Task → RoutingClassifier → ClaudePlanner (plan only, cached prefix)
       → PLAN.md → QwenExecutor (per step) → Validator → Apply
  When: new feature, 3+ files, existing architecture
  Cost: 1 Claude call (short) + N Qwen calls (~$0)

PLAN → EXECUTE → REVIEW
  Same as above + ClaudeReviewer on final diff (compressed context only)
  When: auth/payments/data-loss risk, blast radius score > threshold
  Cost: 2 Claude calls (both short, cached prefix)

PARALLEL
  Task → ClaudePlanner → N independent sub-tasks
       → [QwenExecutor × N] (parallel, file claims enforced)
       → Validator × N → Claude arbiter only if merge conflict
       → Apply
  When: V3, independent microservices, large feature
  Cost: 0-1 Claude calls + N Qwen calls

ESCALATE ON FAILURE
  Task → RoutingClassifier → QwenExecutor (attempt)
       → Validator → FAIL
       → EscalationHandler (Qwen compresses context)
       → ClaudeReviewer (sees compressed failure + task only)
       → QwenExecutor (implements Claude's corrected plan)
       → Apply
  When: unknown upfront complexity, routing was wrong
  Cost: 0 Claude if Qwen succeeds, 1 Claude call if it fails
```

---

### Routing classifier

Implemented as a Qwen call (~200 tokens = ~$0). Emits structured JSON:

```json
{
  "confidence": 0.85,
  "pattern_match": "fastapi-crud-endpoint",
  "missing_context": [],
  "cross_service": false,
  "security_surface": false,
  "route": "direct"
}
```

**Weighted decision matrix:**

| Signal | Weight | Routes to Claude if... |
|---|---|---|
| No existing pattern in codebase | 3.0 | total score > 6 |
| Cross-service dependencies | 2.5 | always additive |
| Security surface (auth/payments/PII) | 2.0 | any keyword match |
| Ambiguity score (NLP on task prompt) | 1.5 | classifier confidence < 0.65 |
| Context size after compression | 1.0 | > 12K tokens remain |

**Confidence threshold:** `confidence < 0.70` → auto-escalate to Claude.
Parse failure of classifier JSON → auto-escalate (safe default).

---

### Escalation mechanism

Qwen embeds self-assessment markers at end of every response:

```xml
<confidence>0.82</confidence>
<needs_context>inventory-service</needs_context>   <!-- unknown dependency -->
<uncertainty>architectural</uncertainty>            <!-- design decision needed -->
```

System-side detection (zero cost, no model call):
- Output contains TODO / FIXME / `pass` placeholder → flag
- Syntax check fails (tree-sitter) → immediate escalate
- Output length < 30% of expected → confused or refused
- 2+ imports unresolvable → escalate

**Escalation flow:**
```
Qwen fails → EscalationHandler
  → Qwen compresses failure context (repo → 3-5K tokens)
  → Claude sees: {compressed_context, original_task, failure_reason}
  → Claude outputs: corrected PLAN.md only (never implementation)
  → Qwen implements the corrected plan
```

---

### Cost optimization strategies

**1. Claude prompt caching**
Structure every Claude prompt as:
```
[CACHED PREFIX — stable across calls]
  System prompt + ADRs + architecture diagram + service registry summary

[VARIABLE SUFFIX — task-specific]
  Task + compressed repo context + specific files
```
Target: 80%+ cache hit rate. Batch related Claude calls within 5-minute windows (Anthropic cache TTL). Cache prefix saves ~70% of Claude input cost on repeated architectural work.

**2. Context compression pipeline (Qwen-only)**
Before any Claude call:
```
raw repo context (50K tokens)
  → Qwen: extract interfaces, types, error boundaries, contracts only
  → compressed context (4-6K tokens)
  → sent to Claude
```
Claude never reads function bodies. Only signatures, contracts, and summaries.

**3. Chunked execution**
Claude sees: `{architecture decision needed, interfaces, constraints}`
Qwen sees: `{interfaces, existing file contents, implementation task}`
Never overlap. Claude input stays small.

**4. Cost gate (pre-execution)**
Before any Claude call:
```
estimated_tokens = count(cached_prefix_tokens) + count(variable_tokens)
estimated_cost   = (uncached_tokens × $0.015 + cached_tokens × $0.0015) / 1000
if estimated_cost > $0.05:
    show: "Claude Opus: ~$0.08 estimated — [proceed / use-qwen / abort]"
```

**5. Pattern learning feedback loop**
When Claude resolves a novel problem → output committed to pattern registry.
Next identical task → RoutingClassifier finds pattern → routes to Qwen (direct).
Claude cost amortized across all future similar tasks (approaches zero over time).

---

### Orchestration module layout

```
orchestration/
├── task_parser.py      # extract entities, service refs, security surface from prompt
├── classifier.py       # Qwen-powered router, emits JSON {route, confidence, ...}
├── cost_gate.py        # token estimation + user confirmation before Claude calls
├── compressor.py       # Qwen-powered context compression (50K → 4-6K)
├── planner.py          # ClaudePlanner: cached prefix + plan-only output
├── executor.py         # QwenExecutor: stateless, emits confidence markers
├── validator.py        # syntax check + marker parse + heuristics, zero cost
└── escalation.py       # EscalationHandler: compress → Claude → Qwen re-execute
```

---

### Full orchestration flow (V1 → V2)

```
devagent run "task"
      │
      ▼
task_parser.py
  extract: service refs, file refs, security keywords, cross-service signals
      │
      ▼
knowledge layer (zero cost)
  ADRs → constraints → active patterns
  check: does a pattern exist for this task?
      │
      ▼
classifier.py  (Qwen, ~200 tokens, ~$0)
  input: task + signals + pattern_match result
  output: {route, confidence, missing_context, cross_service, security_surface}
      │
      ├── route=direct, confidence≥0.70 ──────────────────────────────────────┐
      │                                                                        │
      ├── route=plan_execute ──► cost_gate.py ──► planner.py (Claude)         │
      │                              │                    │                   │
      │                         if cost>$0.05        PLAN.md only             │
      │                         ask user                  │                   │
      │                                                   ▼                   │
      │                                            executor.py (Qwen) ◄───────┘
      │                                                   │
      │                                            validator.py (free)
      │                                              │           │
      │                                            PASS        FAIL
      │                                              │           │
      │                                              │     escalation.py
      │                                              │       │
      │                                              │  compressor.py (Qwen)
      │                                              │       │
      │                                              │  Claude (corrected plan)
      │                                              │       │
      │                                              │  executor.py (Qwen)
      │                                              │       │
      ▼                                              ▼       ▼
safety_rules.py → blast_radius.py → snapshot.py → apply.py → logger.py
                                                       │
                                                 knowledge layer
                                                 write new pattern if novel
```

---

## Architecture

### Directory layout

```
devagent/
├── cli.py                        # typer app — all commands
│
├── orchestration/                # THE ENGINE — who does what, at what cost
│   ├── task_parser.py            # extract entities, signals, service refs
│   ├── classifier.py             # Qwen-powered router (~200 tokens, ~$0)
│   ├── cost_gate.py              # token estimation + user confirm before Claude
│   ├── compressor.py             # Qwen context compression (50K → 4-6K)
│   ├── planner.py                # ClaudePlanner: cached prefix, plan-only output
│   ├── executor.py               # QwenExecutor: stateless, emits confidence markers
│   ├── validator.py              # syntax + marker parse + heuristics (zero cost)
│   └── escalation.py            # compress → Claude plan → Qwen re-execute
│
├── knowledge/
│   ├── adr.py                    # ADR read/write/enforce/lifecycle
│   ├── service_registry.py       # service topology, deps, SLAs
│   ├── pattern_registry.py       # detect patterns + write new ones from Claude output
│   ├── constraint_registry.py    # constraints auto-generated from ADRs + rules.yaml
│   └── rag.py                    # V4: 3-tier semantic retrieval
│
├── planning/
│   ├── blast_radius.py           # impact scoring — runs before every execution
│   ├── task_graph.py             # V2: hierarchical decomposition + dependency graph
│   └── workstream.py             # V3: parallel agent coordination
│
├── context/                      # CONTEXT-SCALE CONTROL — keeps Qwen in its parity envelope
│   ├── index.py                  # Qwen-built repo index: symbols, signatures, dep graph (free)
│   ├── retrieve.py               # assemble ~3 KB exact context per task (attacks repo scale)
│   ├── window.py                 # skeleton+focus windowing for large files (attacks file scale)
│   └── compress.py               # deterministic extraction → Qwen summary; stable for caching
│
├── execution/
│   ├── snapshot.py               # git stash / file copy + undo
│   ├── apply.py                  # diff preview + confirm + write
│   ├── contract.py               # OpenAPI spec gen + oasdiff validation
│   └── lock.py                   # V2: file claim + write lock for parallel agents
│
├── validation/
│   ├── safety_rules.py           # rules engine — evaluated before every write
│   ├── test_runner.py            # V3: run existing tests + auto-rollback
│   └── consistency.py           # V4: detects ADR violations in new code
│
├── models/
│   ├── base.py                   # ModelClient interface (complete, stream, embed)
│   ├── registry.py               # load N models from config; resolve role → client chain
│   ├── router.py                 # role-based selection + fallback (retry, backoff, circuit breaker)
│   ├── openai_compat.py          # llama.cpp + GPT + any OpenAI-compatible endpoint
│   └── anthropic.py              # Claude (Anthropic SDK, cached-prefix management)
│
└── logger.py                     # SQLite: tasks, adrs, service_registry, patterns
```

---

### Data flow — V1

```
devagent run "task"
      │
      ▼
constraint_registry.py
  load active ADRs → extract constraints
  load .devagent/rules.yaml
      │
      ▼
pattern_registry.py
  scan codebase → identify existing patterns
  (how is an endpoint structured here? how are errors handled?)
      │
      ▼
blast_radius.py
  AST import graph + model triage → affected files
  score impact: lines touched, files affected, public APIs changed
  show report → confirm if score > threshold
      │
      ▼
[API task?] → contract.py
  generate OpenAPI spec for new endpoint first
  validate spec is internally consistent
  show spec → confirm before implementation
      │
      ▼
safety_rules.py
  evaluate all rules against planned writes
  BLOCK if any rule fires without override
      │
      ▼
snapshot.py
  git stash push OR copy files to .devagent/snapshots/{ts}/
      │
      ▼
agents/coder.py
  system: repo context + patterns + constraints + ADRs
  user: task + relevant file contents
  output: file edits following detected patterns
      │
      ▼
apply.py
  colored diff preview
  confirm [y/N]  (--dry-run stops here)
  write files
      │
      ▼
logger.py + adr.py
  log task + blast radius + diff + tokens + snapshot
  if architectural decision detected → prompt to capture ADR
```

---

### Data flow — V2 additions

```
devagent run "task"
      │
      ▼
service_registry.py
  load all services + dependency graph
      │
      ▼
router.py
  score: task complexity × file count × cross-service impact
  DIRECT (Qwen) | PLAN_EXECUTE (Claude plan → Qwen exec)
      │
   [PLAN_EXECUTE]
      ▼
blast_radius.py  (service-level)
  which downstream services are affected?
  blast radius score → if high, require explicit confirmation
      │
      ▼
contract.py  (cross-service)
  oasdiff: detect breaking API changes
  buf breaking: detect Protobuf violations
  BLOCK on breaking change without override + audit log entry
      │
      ▼
lock.py
  claim write locks on affected files/services
      │
      ▼
agents/planner.py (Claude)
  input: task + repo graph + service deps + ADRs + patterns
  output: ordered steps [{service, file, action, precondition, postcondition}]
  show plan → human confirm
      │
      ▼
agents/coder.py (Qwen, per step)
  execute each step
  snapshot before each step
  rollback step on failure, report
```

---

### Service registry schema

```yaml
# .devagent/registry/services/{name}.yaml
name: checkout-service
team: payments-team
tech_stack: [python, fastapi, postgresql]
sla_tier: critical          # critical | high | standard
compliance_zones: [pci-dss]

apis:
  produces:
    - spec: ./openapi.yaml
      version: "2.1.0"
  consumes:
    - service: inventory-service
      spec: ../inventory-service/openapi.yaml
      version_pin: ">=1.4.0,<2.0.0"

events:
  produces: [order.created, order.cancelled]
  consumes: [payment.confirmed, inventory.reserved]

databases:
  owned: [orders, order_items]
  read_replicas: [product_catalog]

deployment:
  platform: kubernetes
  namespace: payments
  replicas: { min: 3, max: 20 }
```

---

### ADR format (machine-readable)

```yaml
# .devagent/adrs/0042-paginated-api-cursor.yaml
id: "0042"
title: "Use cursor-based pagination for all list APIs"
status: accepted          # draft | accepted | deprecated | superseded
date: "2026-06-06"
affects_services: [product-service, search-service, checkout-service]

decision: >
  All list endpoints must use cursor-based pagination.
  Offset-based pagination is prohibited on tables > 10k rows.

consequences:
  - All new list endpoints must accept `cursor` and `limit` params
  - Response must include `next_cursor` and `has_more` fields

generates_constraints:
  - id: "C-0042-a"
    rule: "if file matches **/routes/*.py and task contains 'list' then require cursor pagination"
    severity: block         # block | warn | log
```

---

### Safety rules engine

```yaml
# .devagent/rules.yaml
rules:
  - id: no-prod-write-without-snapshot
    description: Always snapshot before writing
    trigger: any_write
    action: block_if_no_snapshot

  - id: auth-service-requires-review
    description: Auth changes need security flag
    trigger: write_to_path("**/auth/**")
    action: require_flag(security-review)

  - id: migration-requires-approval
    description: DB migrations need DBA sign-off
    trigger: write_to_path("**/migrations/**")
    action: require_flag(dba-approved)

  - id: breaking-api-change
    description: Breaking API changes need blast radius report
    trigger: contract_diff(breaking=true)
    action: require_confirmation(blast_radius_report=true)

  - id: pci-zone-isolation
    description: PCI services cannot import from non-PCI services
    trigger: import_added_from(non_pci_zone, pci_zone)
    action: block
```

---

### Task log schema (SQLite)

```sql
CREATE TABLE tasks (
    id              INTEGER PRIMARY KEY,
    created_at      TEXT,
    session_id      TEXT,           -- groups steps in a multi-step task
    task            TEXT,
    service         TEXT,
    files           TEXT,           -- JSON list
    model           TEXT,
    tokens_in       INTEGER,
    tokens_out      INTEGER,
    snapshot        TEXT,
    diff            TEXT,
    blast_radius    TEXT,           -- JSON: {files_affected, apis_changed, score}
    adrs_consulted  TEXT,           -- JSON list of ADR IDs
    rules_evaluated TEXT,           -- JSON: {rule_id, result}
    actual_cost         REAL,       -- real $ spent (Qwen≈0 + any cloud calls made)
    counterfactual_cost REAL,       -- est. $ for same pipeline with frontier executor
    savings             REAL,       -- counterfactual_cost - actual_cost
    quality_gate    TEXT,           -- JSON: {types,tests,coverage_delta,security,lint,conformance}
    in_envelope     INTEGER,        -- 1 if task stayed inside the measured parity envelope
    audit_result    TEXT,           -- JSON|null: {audited, verdict, judge_model}
    status          TEXT            -- pending|applied|rolled_back|dry_run|blocked
);

CREATE TABLE adrs (
    id          TEXT PRIMARY KEY,
    title       TEXT,
    status      TEXT,
    yaml        TEXT,
    created_at  TEXT,
    updated_at  TEXT
);

CREATE TABLE service_registry (
    name        TEXT PRIMARY KEY,
    yaml        TEXT,
    updated_at  TEXT
);
```

---

### Model client interface

```python
class ModelClient:
    def complete(self, system: str, user: str, **kwargs) -> str: ...
    def stream(self, system: str, user: str, **kwargs) -> Iterator[str]: ...
    def embed(self, texts: list[str]) -> list[list[float]]: ...  # V4
```

`llamacpp.py` uses `openai` SDK pointed at `http://localhost:8080/v1`.
Config in `~/.devagent/config.toml` — swap provider/base_url to change model.

---

### Config

```toml
# ── Model registry: declare any number of models, local or cloud ──
[models.qwen-local]
protocol  = "openai-compat"          # llama.cpp OpenAI-compatible server
base_url  = "http://localhost:8080/v1"
model_id  = "qwen3-27b"
tier      = "local"                  # local = ~$0
timeout_s = 120

[models.opus]
protocol = "anthropic"
model_id = "claude-opus-4-8"
tier     = "frontier"

[models.sonnet]
protocol = "anthropic"
model_id = "claude-sonnet-4-6"
tier     = "mid"

# add GPT / any OpenAI-compatible endpoint the same way — no code change:
# [models.gpt]
# protocol = "openai-compat"
# base_url = "https://api.openai.com/v1"
# model_id = "<model-id>"
# tier     = "frontier"

# ── Role → ordered model chain (first = primary, rest = fallback) ──
[roles]
classifier = ["qwen-local"]
executor   = ["qwen-local"]
compressor = ["qwen-local"]
planner    = ["opus", "sonnet"]      # plan-first uses the best; falls back if down
reviewer   = ["opus", "sonnet"]

[fallback]
retries                = 2
backoff_s              = 1.5
circuit_break_after    = 3           # consecutive failures → skip provider during cooldown
local_timeout_fallback = true        # local hang/OOM → fall to next in chain

[model_defaults]
temperature = 0.2
max_tokens  = 8192

[limits]
max_context_files       = 15
blast_radius_warn       = 10    # warn if > 10 files affected
blast_radius_block      = 50    # block if > 50 files affected (require --force)
token_budget_session    = 100000
checkpoint_diff_lines   = 300   # pause for confirm if diff > N lines

[registry]
service_dir = ".devagent/registry/services"
adr_dir     = ".devagent/adrs"
rules_file  = ".devagent/rules.yaml"

[log]
db_path = "~/.devagent/tasks.db"
```

---

## Cost model

| Orchestration pattern | Qwen calls | Claude Opus calls | Estimated cost |
|---|---|---|---|
| Direct | 1 executor | 0 | ~$0 |
| Plan → Execute | 1 classifier + N executors | 1 planner (short, cached) | ~$0.01–0.05 |
| Plan → Execute → Review | same + 1 reviewer (compressed) | 2 | ~$0.05–0.15 |
| Parallel (N agents) | 1 classifier + N executors | 0–1 (conflict only) | ~$0–0.05 |
| Escalate on Failure | 1 classifier + 1 compressor + 1 re-executor | 1 (compressed context) | ~$0.02–0.08 |

**Cost reduction mechanisms:**
1. Classifier uses Qwen (~200 tokens, ~$0) — never Claude
2. Context compression (Qwen) before any Claude call: 50K → 4-6K tokens
3. Claude prompt prefix cached: ADRs + architecture summary cached across calls (5-min TTL, ~70% cost reduction on repeated work)
4. Claude writes PLAN.md only, never implementation bodies — short outputs
5. Pattern learning: Claude cost amortized — once a novel problem is solved and written to pattern registry, all future similar tasks route to Qwen (~$0)
6. `devagent cost` command shows rolling spend per session and total

**Expected real-world split:**
- First week on a new codebase: ~70% Qwen / 30% Claude (patterns being established)
- After patterns established: ~95% Qwen / 5% Claude (only novel architectural decisions)

---

## Tech stack

| Concern | Choice | Reason |
|---|---|---|
| CLI | `typer` | Typed, clean help generation |
| Model calls | `openai` SDK → llama.cpp | llama.cpp speaks OpenAI API; swap by config |
| AST parsing | Python `ast` + `tree-sitter` | `ast` now, tree-sitter adds JS/TS/Go |
| OpenAPI validation | `pydantic` + `openapi-spec-validator` | validate specs before impl |
| API diff | `oasdiff` (CLI tool) | industry-standard breaking change detection |
| Protobuf diff | `buf` (CLI tool) | V2, when proto contracts exist |
| Diff rendering | `rich` + `difflib` | colored terminal diffs |
| Task log | `sqlite3` stdlib | zero deps |
| Config | `tomllib` stdlib (3.11+) | zero deps |
| Embeddings | `sentence-transformers` | V4 semantic RAG |
| Testing | `pytest` | standard |
| Packaging | `pyproject.toml` + `pipx` | installable as global CLI |

---

## Build order — V1 kernel

Build in this order; each step is runnable/testable before the next. The kernel is ~13 files.

**Phase 1: Models (prove the round-trips)**
1. `pyproject.toml` + skeleton + config loader (`~/.devagent/config.toml`)
2. `models/base.py` + `models/openai_compat.py` — Qwen round-trip via llama.cpp (same client later serves GPT/others)
3. `models/anthropic.py` + `models/registry.py` + `models/router.py` — Claude client + config-driven registry (role→model chains, fallback). Operate with 2 models; abstraction supports N.

**Phase 2: Context-scale control (the biggest lever — keeps Qwen in its parity envelope)**
4. `context/index.py` — Qwen-built repo index: symbols, signatures, dependency graph (free, local)
5. `context/retrieve.py` + `context/window.py` — assemble ~3 KB exact context; for large files, skeleton+focus windowing (full target region + signature-map of the rest). Never dump files or whole large files.
6. `context/compress.py` — deterministic extraction (signatures/types/exceptions/interfaces) → then Qwen summary; stable output for cache hits

**Phase 3: Execute → Gate → Escalate (the thesis)**
7. `execute/executor.py` — stateless single-shot Qwen generation; produces file edits
8. `validate/gate.py` — mandatory pipeline: syntax (tree-sitter) → types (mypy) → imports → tests → security (semgrep/bandit). All ~$0. **This gate is the quality source.**
9. `execute/escalate.py` — on gate failure: compress → Claude returns corrected PLAN only → Qwen re-executes. Trigger = validator failure, never confidence.

**Phase 4: Apply safely**
10. `execute/apply.py` — snapshot (git stash / file copy) → diff preview → confirm (`--dry-run`) → atomic write → `undo` / `resume` via durable session state
11. `ledger.py` + `report.py` + `cli.py` — ledger (actual + counterfactual cost, quality-gate result, in-envelope flag); `report.py` computes savings + parity rate; CLI: `run`, `undo`, `resume`, `cost`, `quality`, `calibrate`, `audit`, `status`

**Phase 5: Prove it (cost + quality)**
12. `calibrate.py` — run benchmark tasks on both models; map the parity envelope (task-type × context-size × file-size). Establishes the routing thresholds.
13. `audit.py` — differential quality audit: same task on frontier model, blinded judge compares → measured parity rate. Tunable sampling (QA budget).

**End-to-end test (the V1 acceptance benchmark):**
Run 20 representative tasks on a real Python/FastAPI repo — **including at least one large-file and one large-repo task** — and verify:
- Each task: retrieve/window ≤~3 KB context → Qwen generate → gate runs → apply
- Gate blocks broken/insecure code; Claude invoked *only* on gate failure, sees ≤6 KB
- Kill the process mid-task → `devagent resume <id>` completes it cleanly
- `devagent cost` reports savings (actual vs counterfactual)
- `devagent quality` reports gate pass + sampled parity rate + in-envelope rate

Two headline deliverables: **≥95% of tokens through Qwen** AND **≥95% parity rate on the
audited subset** (quality held). Everything in V1.5+ must beat both without regressing either.
