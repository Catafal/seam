# Concepts — How Seam Works (and Why)

This document explains the ideas behind Seam: the data model, how relationships are
resolved, and why each subsystem is designed the way it is. For the layered module map
and data-flow diagrams, see [`ARCHITECTURE.md`](ARCHITECTURE.md). For configuration, see
[`CONFIGURATION.md`](CONFIGURATION.md).

The guiding thesis: **structural knowledge about a codebase is computable, so an agent
should query it, not reconstruct it.** Everything below serves that thesis.

---

## 1. The index is a graph

Seam parses each source file with tree-sitter and extracts two kinds of records into a
local SQLite database (`.seam/seam.db`):

- **Symbols** — the nodes. Each has a `kind` (`function`, `class`, `method`, `interface`,
  `type`, `field`), a `name`, a `qualified_name` (`Class.method`), a file + line, and
  enrichment fields (`signature`, `decorators`, `is_exported`, `visibility`).
- **Edges** — the directed relationships between symbols. Each has a `source`, a `target`,
  a `kind`, and a `confidence` tier.

A query is graph traversal. "What breaks if I change `init_db`?" is *walk edges whose
target is `init_db`, upstream, to depth N*. "How does the CLI reach the database?" is
*shortest path from `main` to `init_db`*. The graph makes these O(traversal), not
O(grep + read + human inference).

### Why edges are keyed by name, not row id

This is the single most important design decision in Seam, and it looks odd at first:
an edge stores its endpoints as **symbol names** (strings like `init_db` or
`Client.send`), not as foreign keys into the `symbols` table.

The reason is **independent re-indexing**. When the file watcher sees that `db.py`
changed, it must be able to delete that file's symbols and edges and re-insert them
*without touching any other file's rows*. If edges referenced symbol row ids, an edge
from `pipeline.py` into `db.py` would dangle the moment `db.py`'s rows were replaced with
new ids. Name-keying makes the graph a set of **loosely-coupled, per-file fragments** that
reconcile by name at read time — exactly what an incremental watcher needs.

The cost is **homonym collapse**: two different files that both define a `helper()`
share one graph node. Seam accepts this (it is the same trade-off CodeGraph makes) and
mitigates it three ways: receiver-type inference promotes bare names to qualified
`Type.method` targets at index time; import resolution promotes ambiguous edges to
`EXTRACTED` at read time; and the stable `uid` handle (`sha1(abs_path)[:8]:line`) lets a
caller pin one exact homonym when it matters.

For Python and TypeScript/JavaScript, receiver inference also canonicalizes same-file
import aliases before qualification. `from client import Client as C` and
`import { Client as C }` can therefore turn `self.client.send()` / `this.client.send()`
into `Client.send`, not a fake `C.send` node. That alias step is syntactic; the
query-time confidence pass is what decides whether the resulting qualified target is
unique in the visible index. Constructor-owned dependencies count as receiver evidence
when the type is plain and non-nullable: Python `self.client: Client` or
`self.client = Client()` inside `__init__`, and TypeScript constructor parameter
properties or `this.client = new Client()` inside `constructor`. Optional, union,
generic, container, dotted, chained, and unknown receiver evidence is refused so Seam
keeps the bare method target instead of inventing precision.

---

## 2. The fifteen edge kinds

The traversal layer is **kind-agnostic** — it can walk every edge regardless of kind — so
adding a new edge kind makes graph surfaces (`seam_graph_search`, `seam_context`,
`seam_trace`, `seam_flows`, …) aware of the new relationship with little per-tool code.
`seam_impact` is the deliberate exception: default blast-radius reports filter
`raises`/`catches` and `tests` so explicit exception syntax and static test evidence do
not inflate production change-risk tiers. Use `seam_graph_search --edge-kind
raises,catches` for failure-path review and `seam_graph_search --edge-kind tests` for
test-evidence review.

| Kind | Captures | Example | Confidence |
|------|----------|---------|------------|
| `call` | A symbol invokes another | `index_one_file()` calls `init_db()` | EXTRACTED / AMBIGUOUS / INFERRED |
| `import` | A module/symbol import | `from app.db import init_db` | INFERRED (import-kind) |
| `extends` | Subclass → base class | `class Admin(User)` → `Admin extends User` | INFERRED |
| `implements` | Class → interface | `class View implements Renderable` | INFERRED |
| `instantiates` | Construction | `new Client()` / `Client{}` | INFERRED |
| `holds` | A class **stores** a typed field/property | `self.client: Client` → `Server holds Client` | INFERRED |
| `uses` | A function **references** a user type as a parameter | `def show(m: Manager)` → `show uses Manager` | INFERRED |
| `reads` | A field/property is read | `obj.url` (rvalue) → `reads Config.url` | INFERRED |
| `writes` | A field/property is written | `obj.url = x` / `del obj.url` | INFERRED |
| `http_calls` | A symbol has static literal evidence for calling an HTTP route | `fetch("/users")` → `ROUTE GET /users` | EXTRACTED |
| `reads_config` | Code reads a literal config/env key | `os.getenv("DATABASE_URL")` → `CONFIG DATABASE_URL` | EXTRACTED |
| `configures` | A config key describes a runtime resource | `CONFIG DATABASE_URL` → `RESOURCE database DATABASE` | INFERRED |
| `raises` | A symbol explicitly raises or throws an exception | `raise ConfigError(...)` → `raises ConfigError` | EXTRACTED / INFERRED |
| `catches` | A symbol explicitly handles a typed exception | `except ConfigError` → `catches ConfigError` | EXTRACTED / INFERRED |
| `tests` | A test symbol statically exercises or names a production symbol | `test_parse_config` → `tests parse_config` | EXTRACTED / INFERRED |

`call` and `import` are the structural backbone. `extends`/`implements`/`instantiates`
capture object-oriented structure. `holds`/`uses` capture composition and dependency
injection — so changing a type's constructor surfaces the classes that *store* it and the
functions that *receive* it, not only the call sites. `reads`/`writes` capture data-flow —
so renaming a field surfaces every reader and writer, which a pure call graph misses
entirely. `http_calls` connects literal client calls to first-class `route` symbols when
the route target can be represented statically. Absence of an `http_calls` edge means
"not statically observed", not "no runtime HTTP traffic"; Seam deliberately skips dynamic
URL construction and third-party absolute URLs rather than guessing.
HTTP call edges keep `synthesized_by` empty because they are parser/direct extractor
evidence, not post-pass graph synthesis. The extractor channel is stored on
`edges.provenance`, for example `typescript-fetch-literal` or `python-httpx-literal`.
When a local literal points at a route-shaped target that is not declared in the current
index, graph-search previews and architecture evidence expose `route_resolved: false`
and omit route metadata. Treat those as unresolved local HTTP calls, not proof that the
server implements that method/path.
`reads_config` and `configures` connect code to runtime configuration and operational
resources without storing raw config values; Seam persists key names and redacted value
shape only.
`raises` and `catches` are intentionally explicit-only exception evidence. Seam does not
guess runtime propagation through callees or infer thrown variable types; use
`seam_graph_search --edge-kind raises,catches` when you need the static failure-path
surface before changing an error contract.
`tests` edges are static evidence, not runtime coverage. A whole-index post-pass links
test symbols to production symbols when an indexed test directly calls or instantiates a
production symbol, imports one whose name appears in the test name, or has a unique proximity name such as
`test_parse_config` → `parse_config`. Each derived edge stores provenance in
`edges.synthesized_by` (`test-call`, `test-instantiates`, `test-import`,
`test-name-proximity`) and is rebuilt by `seam init`, `seam sync`, and the watcher after
file updates. Absence of a `tests` edge means "not statically observed", not "untested".

`seam_context` exposes the precise `reads`/`writes` split as `field_readers` /
`field_writers` and static test evidence as `test_callers` / `tested_symbols`,
complementing the inclusive `callers` view.

`seam_graph_search` also exposes named recipes for recurring agent questions.
Recipes are transparent intent labels, not a second query engine: each recipe
compiles into typed filters such as `kind`, `edge_kind`, degree thresholds,
`test_scope`, preview settings, and sort mode, then returns the applied defaults,
caller overrides, caveats, required capabilities, and suggested follow-up calls.
See [`graph-search-recipes.md`](graph-search-recipes.md).

`seam_suspects` is the cleanup-review surface built on top of those graph facts. It
uses raw absence signals such as no incoming production edge, then deliberately adds
blockers for public/exported APIs, static tests, routes, config/resource conventions,
field readers and writers, inheritance relationships, imports, and contained-symbol
usage. Its contract is "review this with caution," never "delete this."

### Why composition and field edges are conservative

`holds`, `uses`, `reads`, and `writes` all follow the same **conservatism contract**: emit
an edge only when the target resolves to a **plain user type**. Optionals (`X | None`),
containers (`list[X]`), generics (`Array<X>`), primitives/builtins (`int`, `string`), and
dotted-qualified names (`pkg.Type`) are refused. The principle: **a false negative
(missed edge) is always cheaper than a false positive (wrong edge)**. An agent that trusts
a wrong dependency is worse off than one that misses a speculative one.

---

## 3. Confidence and how edges resolve

Static analysis is uncertain — a call to `parse()` might bind to any of several `parse`
definitions. Seam never hides that uncertainty; it grades it.

| Tier | Meaning |
|------|---------|
| `EXTRACTED` | The target resolves to exactly one symbol. High certainty. |
| `AMBIGUOUS` | The target name matches more than one symbol. Verify by reading. |
| `INFERRED` | Heuristic edge — target not locally resolvable, or a structural/import edge. |

### Read-time resolution order

Confidence is **recomputed at read time** against the live whole-index name map, by
`resolve_edge()`, in a fixed order:

```text
1. Import promotion   — if a same-file import binds the target to exactly one declaring
                        file → EXTRACTED, resolved_by: import.   (the homonym fix)
2. Name-count rule    — count == 1 → EXTRACTED (resolved_by: name-unique)
                        count  > 1 → AMBIGUOUS (resolved_by: name-collision) + a
                                     proximity best_candidate (closest file by path).
3. Builtin check      — fires ONLY at count == 0: a known builtin/stdlib name
                        → INFERRED, resolved_by: builtin.
4. Fallback           — count == 0, not a builtin → INFERRED, resolved_by: unresolved.
```

Step 3's `count == 0` guard is load-bearing: a user-declared name (count ≥ 1) can *never*
be misclassified as a builtin, regardless of the builtin vocabulary. This is why writing
your own `def get()` keeps a normal resolution.

### Why read-time, not index-time

If confidence were frozen at index time, the watcher's per-file re-index would leave the
rest of the graph's confidence stale — a newly-added second `parse()` should turn existing
`parse` calls AMBIGUOUS everywhere, but those edges live in unchanged files. Resolving at
read time against the current name-count map keeps the whole graph correct after any
single-file edit, with no backfill and no write amplification.

### The weakest-hop rule

A multi-hop path is **only as strong as its weakest edge**. `seam_trace` aggregates
path confidence as `min(EXTRACTED, AMBIGUOUS, INFERRED)` along the hops, and when several
paths reach a symbol at the same distance, the strongest is reported. `resolved_by` on a
path entry reflects the hop that produced that weakest link.

### Provenance: `resolved_by`, `synthesized_by`, and `edges.provenance`

Every resolved edge can explain itself. `resolved_by` says *how the tier was decided*
(`import`, `name-unique`, `name-collision`, `builtin`, `unresolved`). `synthesized_by`
(see §6) says *whether the edge was statically extracted (`null`) or produced by the
synthesis post-pass* (a channel name). Together they let an agent distinguish a hard,
unambiguous `call` from a heuristic, synthesized one — and weight its trust accordingly.
`edges.provenance` is different: it stores direct extractor evidence channels without
turning the edge into a synthesized edge. HTTP call edges use it for literal client
evidence such as `typescript-fetch-literal` or `python-httpx-literal` while
`synthesized_by` stays `null`. Exact receiver-qualified call edges use
`python-receiver-type`, `typescript-receiver-type`, or `javascript-receiver-type`.
Graph search applies query-visible symbol counts to those provenance tags at read time:
when the qualified target exists once in the selected `test_scope`, confidence is
surfaced as `EXTRACTED`; duplicate qualified targets are surfaced as `AMBIGUOUS`;
missing targets stay at the stored conservative confidence.

---

## 4. Risk tiers — turning the graph into an action

Raw dependents are noise; an agent needs *what to do*. `seam_impact` and `seam_changes`
bucket upstream dependents by graph distance into action-oriented tiers:

| Tier | Distance | Action |
|------|----------|--------|
| `WILL_BREAK` | d = 1 | Direct callers/holders/importers — **must update**. |
| `LIKELY_AFFECTED` | d = 2 | Indirect dependents — **should test**. |
| `MAY_NEED_TESTING` | d ≥ 3 | Transitive — test if on a critical path. |

`seam_changes` parses the git diff into changed line ranges, maps ranges to symbols, runs
`impact()` on each, and rolls the highest tier up into an overall `low` → `medium` →
`high` → `critical` verdict (attenuated when the only dependents are AMBIGUOUS).

`seam_affected` reuses the same upstream traversal but filters to **test files** — the
last-mile answer "given my diff, which tests should I run?" — because upstream dependents
*are* "who would break", and the impact layer already tags each entry `is_test`.

`seam_plan` is the action-planning layer over those primitives. In target mode, it starts
from one symbol and combines local context, upstream dependents, relationship evidence, and
indexed test callers into a ranked list of symbols to inspect. In diff mode, it starts from
the current git change set and combines changed-symbol risk with affected test files. The
planner never executes tests and never claims runtime proof; it returns explicit caveats,
omitted counts, and follow-up calls so an agent can decide what to read next and which test
command to run.

`seam_suspects` uses the same anti-false-safe posture for cleanup. A strong candidate only
means the index saw multiple weak-connection signals and no known blockers; a weak candidate
may still be important because dynamic dispatch, framework loading, reflection, generated
code, or external consumers can be invisible to static extraction.

---

## 5. Clusters — functional areas

Beyond pairwise edges, an agent often wants the *shape* of the codebase: "what are the
functional areas, and what is this symbol's neighborhood?" Seam answers with **community
detection**.

A pure-Python **Louvain** pass (greedy modularity maximization, stdlib only, deterministic)
partitions the name-keyed edge graph into communities. Each becomes a cluster with a label:

- **Deterministic** (default): `dominant_dir/file — highest-degree symbol`, skipping
  generic scaffolding directories (`src`, `lib`, `app`, …) so `render/src/widget.py` labels
  as `render`, not `src`.
- **LLM** (opt-in, `SEAM_CLUSTER_NAMING=llm`): a small model names the cluster — but only
  at `seam init`, never on the read path, so the server stays 100% local.

Two design notes that matter:

- **Synthesized edges are excluded from clustering.** Synthesis (§6) runs *after*
  clustering and its edges persist in the table; feeding heuristic over-approximations
  back into the next Louvain pass would let them re-partition unrelated modules. Clusters
  therefore reflect only statically-extracted coupling.
- **Clusters are global.** One new edge can re-partition unrelated communities, so there is
  no correct *incremental* cluster update. `seam init` and `seam sync` recompute the whole
  partition (sync gates it on whether the graph actually changed); the per-file watcher
  does **not** — so cluster labels can lag after live edits until the next sync.

`seam_structure` is the **physical** complement to this **semantic** view: it answers
"where does X live?" with the filesystem tree, while `seam_clusters` answers "what is X
coupled to?" Both share the same `area` label, so a file annotated `area: "auth"` in the
structure tree is the Louvain community a maintainer would recognize.

---

## 6. Edge synthesis — seeing what the parser can't

Static parsing cannot see runtime polymorphism. A call to a base-class method, an element
pulled out of a collection and invoked, or a handler fired by an event bus has **no
statically-resolvable call edge** — so `seam_impact` on the concrete implementation would
show an empty upstream, which is dangerously misleading.

Edge synthesis is a **deliberate over-approximation**: a whole-graph post-pass that writes
the edges static parsing structurally cannot infer. The cost of a false-positive
synthesized edge (a slightly wider blast radius) is accepted as far cheaper than a missed
dependency (a silent break). Three channels:

- **A2 — interface→implementation fan-out.** Every base/interface method is linked to
  *every* same-name implementation. Not MRO-resolved — it fans out to all candidates
  (bounded by `SEAM_SYNTHESIS_FANOUT_CAP`), so changing a base method surfaces all
  implementors.
- **A1a — closure-collection dispatch.** When a collection is both iterated and has its
  elements invoked, the collected callables (paired to their append sites by field name)
  are linked to the invocation site.
- **A1b — event-emitter dispatch.** Registrar verbs (`on`/`subscribe`/`addListener`) are
  matched to dispatcher verbs (`emit`/`dispatch`/`publish`) keyed by the event-string
  literal, linking handler ↔ emit site.

Synthesized edges are stored as ordinary `call` edges at `INFERRED` confidence, tagged in
the `edges.synthesized_by` column with the channel that produced them. Because traversal is
kind-agnostic, every tool picks them up automatically; because they are tagged, an agent
can tell a synthesized edge from a static one. Like clusters, they are recomputed only by
`seam init` / `seam sync`, not the watcher.

---

## 7. Semantic search — closing the vocabulary gap

Keyword search fails when the query and the code share no token: searching `"retry logic"`
won't find `_backoff_with_jitter`. Seam closes this with **opt-in local embeddings**.

At index time (`seam init --semantic`), each symbol's name + signature + docstring is
embedded with a small quantized model (fastembed, ONNX on CPU — no GPU, no torch, no API
key) and the vector is stored in the index. At query time the query is embedded locally and
cosine-compared against stored vectors; the semantic candidates are merged with FTS5
candidates via **Reciprocal Rank Fusion** (RRF, k=60). The model downloads once (~67 MB),
then the read path is **100% local**.

Design guarantees: it is **off by default** (a keyword-only index behaves byte-identically
to pre-semantic), and a **model-mismatch guard** detects when the stored vectors came from a
different model than is configured and falls back to FTS-only rather than silently mixing
incompatible vector spaces.

Agent contract: semantic similarity is a **discovery lead**, not dependency proof.
`seam_schema` reports semantic readiness (`disabled`, `unavailable`, or `usable`) with the
exact fallback reason before an agent spends a search round trip. `seam_search` and
`seam_query` expose `retrieval_mode`, `retrieval`, `caveats`, and
`recommended_next_calls` on each result. Semantic-only hits carry a verification caveat and
point to `seam_snippet`, `seam_context`, and `seam_plan`; graph/risk/doc tools do not consume
embeddings and do not treat semantic similarity as an edge.

---

## 8. Staleness detection — never silently wrong

A blast-radius answer computed against a stale index is worse than no answer — the agent
trusts it. So the five graph-traversal tools (`seam_impact`, `seam_changes`,
`seam_affected`, `seam_context`, `seam_trace`) attach an `index_status` banner when the
index has drifted from disk:

```json
{ "index_status": { "stale": true, "reason": "...", "hint": "run seam sync" } }
```

The banner is **absent when fresh** — its presence is the signal. The check is a single
source of truth (`seam/analysis/staleness.py`, also used by `seam status`) with three
deliberate properties:

- **Bounded.** It stats only the newest `SEAM_STALENESS_SCAN_CAP` (200) files by
  `indexed_at`, keeping the hot read path sub-millisecond on large repos. (The trade-off:
  a stale file outside that window may go undetected.)
- **Watcher-aware and synth-aware.** A live watcher self-heals file drift, so file-mtime
  staleness is suppressed when it is running — *but* a watched index with synthesized edges
  is still flagged, because the watcher never recomputes synthesis or clusters.
- **Conservative — never cry wolf.** Any IO/DB error returns `stale = False`. A correctness
  feature that false-alarms trains agents to ignore it; silence on error is the safer
  failure mode. Only *stale* verdicts are cached (TTL), so a fresh→stale transition is
  detected on the next call rather than masked by a cached "fresh".

---

## 9. Output discipline — an agent-facing tool budgets its own output

A tool whose output blows the agent's context window is a net loss — the canonical example:
`seam_impact` on a hub symbol once returned ~30k tokens, worse than just reading the files.
Seam treats output size as a first-class concern:

- **Per-tier caps.** `seam_impact` returns a full `risk_summary` histogram (honest totals)
  but caps each tier's *entries* (`SEAM_IMPACT_MAX_RESULTS`, default 25), reporting
  `truncated` counts. The histogram stays trustworthy; the entry list stays small.
- **Relevance ordering before the cap.** External dependents are ranked ahead of the
  target's own sibling members before truncation, so the cap drops self-references first
  and the highest-signal external dependents survive.
- **Lean mode.** `verbose=false` (`--lean`) drops heavy provenance fields where records
  repeat (biggest win on `seam_trace`).
- **Hard byte ceiling.** `SEAM_IMPACT_MAX_BYTES` trims entries from the least-valuable end
  (downstream before upstream, MAY_NEED_TESTING before WILL_BREAK) until the serialized
  response fits a character budget — because agents budget in tokens, not entry counts.
- **Truncation steer + anti-false-safe.** When entries are trimmed, `next_actions` names
  the exact remedy ("raise limit to 17 to see 12 more WILL_BREAK dependents"). When a byte
  budget trims *everything*, the output explicitly says "trimmed to fit" — never "no
  dependents found" — so an agent can't conclude a symbol is safe to delete when its
  dependents were merely budgeted away.

`seam_context_pack` applies the same philosophy to neighbor lists, ranking callers/callees
by **personalized PageRank (random-walk-with-restart)** relevance to the seed before
capping — so a capped bundle keeps the neighbors most woven into the seed's local
neighborhood, not the lowest-id ones. The pack also includes bounded
`relationship_evidence` records for direct caller/callee claims, plus `caveats` and
`recommended_next_calls`, so an agent can distinguish "indexed static evidence says this"
from "runtime behavior is proven."

`seam_plan` carries the same budget discipline into planning: it caps inspection items,
test files, and enriched target evidence independently, then reports `omitted` counts and a
caveat when a cap hides lower-ranked work. The planner's output is intentionally smaller
than chaining `context_pack`, `impact`, and `affected` manually; when an agent needs exact
source, the plan points to `seam_snippet` rather than embedding implementation bodies.

`seam_suspects` caps candidates, evidence, and signals separately so cleanup review remains
agent-readable without hiding uncertainty. When caps apply, the response includes caveats
and follow-up calls instead of implying the visible candidates are exhaustive.

---

## 10. Freshness — init, watch, sync

Three mechanisms keep the index aligned with the source:

- **`seam init`** — full re-index from scratch. Runs the whole pipeline plus the clustering
  and synthesis post-passes. The escape hatch for any staleness.
- **The watcher** (`seam start`) — a debounced `watchdog` daemon that re-indexes individual
  files on save. Keeps symbols/edges fresh in real time, but does **not** recompute clusters
  or synthesized edges (both are global post-passes).
- **`seam sync`** — one-shot filesystem reconcile (mtime pre-filter → SHA-1 confirm). Indexes
  added files, re-indexes changed ones, removes vanished ones (guarded so a transient walk
  hiccup can't wipe the index), then recomputes clusters/synthesis **gated** on whether the
  graph actually changed. The complement to the always-on watcher for catching
  pull/checkout/merge changes in bulk.

The accepted blind spot (shared with comparable tools): a content change that preserves the
file's mtime *exactly* is missed by `sync` — `seam init` is the escape hatch.

---

## Per-language notes

All twelve languages share the same tools and edge kinds, but extraction fidelity varies by
what each grammar exposes:

- **`.h` maps to C, not C++.** Mixed projects use `.h` for both; routing to C handles the
  common case (structs, typedefs, prototypes). Use `.hpp`/`.hh`/`.hxx` for C++ headers.
- **C++ method visibility is `null`** — in-class access specifiers aren't yet threaded to
  individual symbols. Java/C#/PHP visibility is extracted correctly.
- **Ruby visibility is `null`** — `private`/`protected` are runtime DSL calls, not static
  AST nodes. `is_exported` is also `null` (dynamic).
- **Java/C#/PHP import resolution returns `[]`** for qualified package/namespace paths —
  classpath/NuGet/Composer layout is unavailable at index time, so cross-package calls fall
  back to the name-count rule. Same-repo unique names still resolve to EXTRACTED.
- **Go module-qualified imports** (`github.com/org/repo/pkg`) resolve via `go.mod`'s module
  prefix; true third-party paths correctly return no match.
- **Nested classes have flat qualified names** (`Inner`, not `Outer.Inner`) — consistent
  with the name-keyed edge graph.
- **Ruby emits no `uses` edges** (dynamically typed → no parameter types to bind).

---

*For the module-by-module map, the write/read data-flow, and the schema, continue to
[`ARCHITECTURE.md`](ARCHITECTURE.md).*
