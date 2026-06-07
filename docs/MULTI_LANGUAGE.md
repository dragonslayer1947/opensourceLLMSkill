# Multi-language continuity (gap #4) — phased plan

**Goal:** extend devagent's *continuity machinery* — the parts that keep a large codebase from
breaking — beyond Python to JS/TS, Go, and more. Today the index, blast radius, interface
resolution, service edges, and impact-test selection are AST-precise for Python; other languages
fall back to keyword retrieval with none of the continuity guarantees.

The honest framing until this is complete: **the continuity guarantees are proven for Python; other
languages get progressively more.**

## What each layer needs per language
| Layer | Needs from the language |
|---|---|
| Retrieval ranking | symbols (names/signatures) + content terms |
| Blast radius / impact tests | the import/dependency graph (who imports whom) |
| Interface contracts (`verify`) | exported names per module + cross-file import resolution |
| Cross-service edges (#3) | route decorators/handlers + http-client calls + pub/sub APIs |

## Phases

### ✅ Phase 1 — JS/TS into the import graph (shipped)
A heuristic, dependency-free analyzer (`_parse_js` in `context/index.py`):
- extracts **exported symbols** (`export function/class/const`, `export { … }`) → feeds retrieval;
- extracts **import specifiers** (`import … from`, `require()`, dynamic `import()`, `export … from`);
- **resolves relative specifiers** to repo files (`./x`, `../y`, dir→`index.*`, extension probing) →
  `FileEntry.import_targets`, merged into `build_dependents`.

Result: **blast radius, dependent analysis, and symbol-aware retrieval now span JS/TS**, with no new
dependency. Limitation: regex-based, so it sees the common 90% (ES modules / CommonJS) but not
re-exports through barrels, path aliases (`tsconfig` `paths`), or computed requires.

### Phase 2 — Tree-sitter parsing (precision)
Replace the regexes with [tree-sitter](https://tree-sitter.github.io) grammars (via the
`tree_sitter_languages` wheel — prebuilt, no compiler) behind the same seam:
- a `LanguageAnalyzer` protocol (`symbols`, `imports`, `import_targets`, `routes`, `topics`);
- accurate symbol ranges (real `end_lineno`) so large-file windowing focuses correctly;
- handles re-exports, default/named distinctions, TS types/interfaces.
Python keeps its `ast` analyzer; everything else routes through tree-sitter. Optional dependency:
absent → fall back to the Phase-1 heuristics, never a hard failure.

### Phase 3 — Cross-file interface check per language
Generalize `validate/interface.py` (today Python-only `from x import y` resolution) to JS/TS using
the tree-sitter export table: flag an import of a name a module doesn't export → `devagent verify`
catches interface drift in JS/TS too. Add module-resolution config (`tsconfig paths`, `package.json`
`exports`).

### Phase 4 — Cross-service edges per framework (#3 for JS/Go)
Framework-aware route/topic extraction: Express/Fastify/NestJS routes, Go `net/http` + chi/gin,
client calls (`fetch`, `axios`), and queue SDKs — so the cross-service blast radius (gap #3) is no
longer Python/FastAPI-only.

## Design invariant
Every phase sits behind the analyzer seam keyed by file extension and **degrades gracefully**: an
unsupported language or a missing optional dependency falls back to the previous tier (heuristics →
keyword retrieval), never an error. Python's AST path is never regressed.
