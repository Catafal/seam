# PRD — Phase 2: Graph community detection (clustering)

> Phase 2. Implements the "Leiden/Louvain clustering is Phase 2" deferral from DISCOVERY.md
> and the "optional LLM naming layer" consequence of ADR-003. Status: ready-for-agent.

## Problem Statement

As an AI agent (or developer) exploring an unfamiliar codebase through Seam, I can find a single
symbol (`seam_search`), see its immediate neighbors (`seam_context`), and trace a path between two
symbols (`seam_trace`) — but I have no sense of the codebase's **functional areas**. I cannot ask
"what cohesive group of code does this symbol belong to?" or "show me the auth subsystem" or "what
are the major modules in this repo?". `APP_FLOW.md` already promises that `seam_query` surfaces
"symbols in the same cluster," but nothing computes clusters today, so that promise is unfulfilled.
Without a higher-level map, I burn tokens re-deriving structure that the call graph already implies.

## Solution

As an agent, I want Seam to partition the symbol graph into **clusters** (functional areas /
communities) during `seam init`, give each cluster a human-readable **label**, and let me:

- list the codebase's clusters (`seam_clusters` → `[{id, label, size}]`),
- list the members of one cluster (`seam_clusters` with a cluster id → member symbols),
- and see, for any symbol, which cluster it belongs to and its **same-cluster peers**
  (enriched `seam_context`).

Clusters are computed with a deterministic, pure-Python community-detection algorithm (no new
runtime dependencies, no external services). Cluster **labels** default to a deterministic
heuristic; an **opt-in** LLM naming layer (off by default, runs only at index time) can produce
nicer names when explicitly configured.

## User Stories

1. As an agent, I want the symbol graph partitioned into clusters during `seam init`, so that functional areas are precomputed and queries are instant.
2. As an agent, I want clustering to use a deterministic algorithm, so that a symbol does not jump between functional areas every time the index is rebuilt (stable, trustworthy answers).
3. As an agent, I want clustering to add **zero new runtime dependencies** and make **zero network calls by default**, so that Seam stays local-first and the "zero external services" guarantee holds.
4. As an agent, I want a `seam_clusters` tool with no argument to return all clusters as `[{id, label, size}]`, so that I can see the major functional areas of a codebase at a glance.
5. As an agent, I want `seam_clusters` with a cluster id to return that cluster's member symbols (name, file, line, kind), so that I can drill into one functional area.
6. As an agent, I want `seam_context` enriched with the symbol's `cluster_id`, `cluster_label`, and a list of `cluster_peers`, so that I can navigate "what else lives with this symbol" without a second call.
7. As an agent, I want an empty/whole result (not an error) when the index has no clusters yet (e.g. an index built before this feature, or an empty repo), so that "no clusters" is a clean, distinguishable answer.
8. As a developer, I want a `seam clusters` CLI command (list all, or members of one) mirroring the MCP tool, so that I can inspect clusters from the terminal.
9. As a developer, I want `seam status` to report the cluster count, so that I can see at a glance whether the index has clustering data.
10. As an agent, I want each cluster to carry a deterministic label derived from its dominant file/directory and its most-connected symbol (e.g. `seam/analysis — traversal.walk`), so that even with no LLM I get a meaningful name.
11. As a developer, I want an **opt-in** LLM naming layer, off by default, that names clusters only when I explicitly enable it and provide an API key, so that I can get higher-quality names without Seam ever calling out unless I ask it to.
12. As a developer, I want the LLM naming call to happen **only during `seam init`** (a build step), never in the MCP read path, so that the running MCP server is always 100% local even when LLM naming is enabled.
13. As a developer, I want LLM naming to **fail safe** — any error (no key, network failure, bad response) falls back to the deterministic label and never aborts `seam init` — so that one flaky call can't break my index.
14. As a maintainer, I want cluster assignments stored on the symbols table (`cluster_id`) and cluster metadata in a `clusters` table, so that lookups are O(1) joins.
15. As a maintainer, I want a guarded, additive schema migration to v4 that adds the `clusters` table and `symbols.cluster_id` without destroying existing data, and that tells the user to re-index to populate it, consistent with the v1→v2 and v2→v3 migrations.
16. As a maintainer, I want community detection implemented as a **deep, pure module** (graph in → cluster map out, no SQLite, no I/O), so that it is unit-testable in isolation and the algorithm can be swapped without touching persistence.
17. As a maintainer, I want clustering to never raise during `seam init` — on any internal error it logs and leaves clusters unpopulated (every symbol `cluster_id = NULL`) rather than aborting the index — consistent with the "parsers never raise / indexer skips gracefully" rule.
18. As an agent, I want clusters computed over the **whole** post-index graph (all files), so that cross-file functional areas emerge, not per-file fragments.
19. As a maintainer, I want clustering config read only from `seam/config.py` (naming mode, optional key/model, optional min-cluster-size), so that no module reaches into `os.getenv` directly.
20. As an agent, I want singleton symbols (no edges) handled gracefully — either each in its own trivial cluster or grouped by file — so that disconnected nodes don't crash detection or produce a meaningless giant "misc" bucket silently.
21. As an agent, I want `seam_clusters` and the enriched `seam_context` to relativize file paths to the project root, consistent with all other tools.
22. As a maintainer, I want a new ADR recording the pure-Python Louvain choice and the opt-in LLM naming design, and ADR-003 updated to note the naming layer is now implemented as opt-in.

## Implementation Decisions

- **Algorithm — pure-Python Louvain (greedy modularity maximization).** Detection runs over an
  in-memory undirected graph built from the `edges` table (call + import edges), with all `symbols`
  as the node set. No new dependency (`networkx`/`igraph`/`leidenalg` were rejected to preserve
  "zero external services" and "simplest"). **Determinism is mandatory:** nodes are processed in a
  fixed sorted order and ties are broken deterministically, so the same graph always yields the same
  partition. A simple label-propagation pass is acceptable only as an internal fallback if a degenerate
  graph stalls modularity — the public contract is "deterministic communities," not a specific internal step.
- **Deep detection module.** Community detection is a pure function: `nodes` + `edges` in →
  `{symbol_name: cluster_id}` out. No SQLite, no file I/O, no config. This is the deep, isolated,
  trivially-testable core. Persistence and labeling are separate layers.
- **Disconnected / singleton nodes.** Symbols with no edges are assigned to their own single-member
  cluster (deterministic). Tiny clusters below an optional `min cluster size` may be folded into a
  per-file or "ungrouped" bucket — but the MVP default keeps every node assigned (no silent dropping).
- **Labeling — deterministic by default, opt-in LLM.** A `cluster naming` layer computes a label for
  each cluster. Default ("deterministic"): derive from the cluster's dominant directory/file prefix +
  its highest-degree symbol (e.g. `seam/analysis — traversal.walk`). Opt-in ("llm"): when explicitly
  enabled in config AND an API key is present, call an LLM at index time with the cluster's member
  names/paths to get a short label; the `clusters` table records which naming source was used. The
  LLM call uses the stdlib HTTP client (no SDK dependency) and is fully isolated in the naming module.
- **Fail-safe naming.** LLM naming is wrapped so that a missing key, network error, timeout, or
  malformed response logs a warning and falls back to the deterministic label. `seam init` never
  fails because of naming. With naming disabled (the default), no network code path is reached at all.
- **Compute at index time, stored.** Clustering is a **post-pass** in `seam init`, after the
  per-file index loop completes, over the whole graph — NOT inside `index_one_file` (which is per-file
  and shared with the watcher). The watcher does not recompute clusters; per-file edits leave
  `cluster_id` as last computed (or NULL for new symbols) until the next full `seam init`. This
  staleness is documented and mirrors git's index model. (Recompute-on-watch is out of scope.)
- **Schema v4 (guarded, additive migration).** Add a `clusters` table (`id` PK, `label` text,
  `size` int, `naming_source` text — `deterministic`|`llm`) and a nullable `cluster_id` column on
  `symbols` (no FK churn; cleared and repopulated by the clustering post-pass). `schema.sql` creates
  both via `CREATE TABLE IF NOT EXISTS` / guarded `ALTER TABLE`; a `_run_migration_v3_to_v4` guard
  bumps `schema_version` to `'4'` exactly once and logs a re-index hint. Fail-loud on migration error,
  like the existing guards. Fresh DBs are seeded at version `'4'`.
- **Persistence/orchestration layer.** A new indexer-layer function reads symbols+edges from the
  connection, calls the pure detection module, calls the naming layer, then writes the `clusters`
  rows and updates `symbols.cluster_id` — all in one transaction. This bridges the pure analysis
  module and the db writer without making the analysis layer import db (import hierarchy preserved:
  `cli → indexer(cluster orchestration) → analysis.clustering` + `db`; analysis stays pure).
- **Read path.** A new query-layer module (mirroring `query/comments.py`) exposes
  `list_clusters(conn) -> [{id, label, size}]`, `cluster_members(conn, id) -> [member rows]`, and
  `cluster_peers(conn, symbol) -> (cluster_id, label, peer_names)` for context enrichment. Read-only,
  query/analysis layer, no server/cli imports. Guards a pre-v4 index (missing table/column) by
  returning empty results + a one-time warning, exactly like `_comments_table_exists` in `why()`.
- **MCP + CLI surfaces.** New `seam_clusters` MCP tool (`cluster_id: int | None`; no id → list all;
  id → members). `handle_seam_context` enriched to include `cluster_id`, `cluster_label`,
  `cluster_peers`. New `seam clusters` CLI command (list / `--id` members). `seam status` gains a
  cluster count row. Handlers relativize file paths to the project root, consistent with all tools.
- **Config additions (config.py only).** Naming mode (default deterministic / opt-in llm), optional
  LLM API key + model, and an optional min-cluster-size. No `os.getenv` outside config.py.
- **Docs.** New ADR (pure-Python Louvain + opt-in LLM naming); update ADR-003 (naming layer now
  implemented, opt-in); replace the stale "Phase 2 Extensions" section in `ARCHITECTURE.md` (those
  three bullets shipped in Phase 1b); add `seam_clusters` + context fields to `mcp-tools.yaml`;
  note the contract in `CONTRACT.md`; update `BACKEND_STRUCTURE.md` module map.

## Testing Decisions

- **What makes a good test:** assert external behavior. Given a small graph (nodes + edges), the
  detection module returns the expected partition and returns it **identically on a second run**
  (determinism). Given a seeded in-memory DB, the read functions return the right clusters/members/
  peers. Naming is tested through its public function with the LLM path **monkeypatched** (never a
  real network call) — verifying the deterministic label, the LLM-on label, and the fail-safe fallback.
- **Modules tested:**
  - Detection (pure): a graph with two clear communities → two clusters; determinism (run twice →
    identical map); two disconnected components → two clusters; a single node → one cluster; empty
    graph → empty map; never raises on degenerate input.
  - Naming: deterministic label from members → expected string; with LLM enabled (stubbed) → uses the
    stubbed name and records `naming_source = 'llm'`; with LLM raising → falls back to deterministic
    and records `deterministic`; with naming disabled → no network code reached.
  - Read layer: `list_clusters` / `cluster_members` / `cluster_peers` against a seeded DB; pre-v4
    index (no table/column) → empty results, no raise.
  - Migration: opening a v3 DB bumps `schema_version` to `'4'` and the `clusters` table + `cluster_id`
    column exist; idempotent on a v4/fresh DB.
  - Engine/handler/CLI wiring: `context()` includes cluster fields when assigned (and clean
    null/empty when not); `seam_clusters` relativizes paths; `seam init` on the fixtures produces ≥1
    cluster; `seam status` shows a cluster count.
- **Prior art:** `tests/unit/test_confidence_global.py`, `test_traversal_global_confidence.py`,
  `test_seam_why.py`, `test_changes_partial.py`; `tests/integration/test_qa_hardening_wiring.py`,
  `test_seam_why_handler.py`; the v1→v2 / v2→v3 migration test pattern; the `query/comments.py`
  guarded-pre-feature-index pattern.
- **TDD:** write the failing detection test first (two-community graph → two clusters, plus the
  determinism assertion), then build detection, then naming, then persistence, then the read layer
  and surfaces.

## Out of Scope

- **Incremental / watcher cluster recompute** — clustering runs only on full `seam init`; the watcher
  does not recompute. Documented staleness.
- **Hierarchical / nested clusters** — flat partition only (no dendrogram, no sub-clusters).
- **Leiden's refinement step** — Louvain (greedy modularity) only; the ADR notes Leiden as a future option.
- **Configurable algorithm selection** at runtime — the algorithm is fixed for this slice.
- **LLM-named execution flows / clusters beyond the label string** — only a short cluster label is
  LLM-generated; no summaries, no flow narration.
- **FTS over cluster labels** — cluster lookup is by id / by symbol, not full-text.
- **Cross-repo / multi-index clustering** — single index only.
- **`seam_query` re-ranking by cluster** — the APP_FLOW "same cluster" navigation is delivered via the
  enriched `seam_context` (`cluster_peers`); changing `seam_query`'s ranking is a separate slice.

## Further Notes

- The "Phase 2 Extensions (planned)" list in `ARCHITECTURE.md` is stale: its three bullets (semantic
  comment nodes, Go+Rust parsers, cross-file confidence) all shipped in Phase 1b. This slice replaces
  that section with the clustering design.
- Reconciling "optional LLM naming" with "zero external services at runtime": the LLM call lives in
  `seam init` (a user-initiated build step), never in the MCP server's read path, and is off by
  default. The running server is always local; the deterministic label is always the fallback.
- Determinism is the load-bearing property: a code-intelligence tool that reshuffles functional areas
  on every rebuild is untrustworthy. The pure detection module's tests must assert byte-identical
  output across runs on the same input.
- After merge, existing indexes must run `seam init` once to populate clusters (the migration creates
  the table/column empty).
- Acceptance: `seam init` partitions the graph into deterministic clusters with labels; `seam_clusters`
  lists clusters and members; `seam_context` shows cluster + peers; LLM naming is opt-in, fail-safe,
  and index-time only; schema migrated to v4; new ADR added; `make gate` green.
