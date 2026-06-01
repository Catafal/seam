# ADR-007: Pure-Python Louvain Community Detection + Opt-In LLM Naming

**Status:** Accepted  
**Date:** 2026-06-01  
**Phase:** 2 (Graph community detection)

---

## Context

Seam needed a way to partition the symbol graph into functional areas (clusters)
so agents can ask "what cohesive group does this symbol belong to?" and navigate
subsystems without manually deriving structure from individual symbols.

Requirements:
- Zero new runtime dependencies (preserved "zero external services" guarantee)
- Deterministic output — same graph → same cluster IDs every rebuild
- Pure function: testable in isolation, swappable without touching persistence
- Cluster labels: human-readable with no LLM required; LLM naming opt-in

## Decision

**Algorithm:** Pure-Python Louvain greedy modularity maximization (Phase 1 only).
No `networkx`, `igraph`, or `leidenalg`. Implemented in `seam/analysis/clustering.py`.

**Key implementation choices:**
- Nodes processed in sorted order; tie-breaking by community label (alphabetical) ensures determinism
- Self-loops silently ignored
- Edges to unknown nodes silently ignored (never raises)
- Each run: assign stable integer IDs sorted by the minimum member name of each community

**Labeling:** `seam/analysis/cluster_naming.py` produces a `deterministic` label
(`dominant_dir/file — highest_degree_symbol`) by default. An `llm` mode (opt-in via
`SEAM_CLUSTER_NAMING=llm` + `SEAM_LLM_API_KEY`) calls an OpenAI-compatible endpoint
using stdlib `urllib` only, and records `naming_source` in the `clusters` table.
Any LLM error falls back to deterministic silently — `seam init` cannot be aborted by naming.

**LLM path isolation:** LLM naming runs ONLY during `seam init` (post-index build step),
never in the MCP server's read path. The running server is always 100% local.

## Consequences

- Added `seam/analysis/clustering.py` (pure), `seam/analysis/cluster_naming.py` (pure + isolated LLM)
- Added `seam/indexer/cluster_index.py` (orchestration bridge)
- Added `seam/query/clusters.py` (read layer with pre-v4 guard)
- Schema bumped to v4: `clusters` table + `symbols.cluster_id` column
- `seam/query/engine.py::context()` enriched with `cluster_id`, `cluster_label`, `cluster_peers`
- New `seam_clusters` MCP tool and `seam clusters` CLI command
- `seam status` shows cluster count
- Config: `SEAM_CLUSTER_NAMING`, `SEAM_LLM_API_KEY`, `SEAM_LLM_MODEL`, `SEAM_CLUSTER_MIN_SIZE`

**Known limitation — name-based graph, cross-file homonyms collapse.**
The community detection graph is keyed on symbol *name* (not on `(file, name)` composite keys), which matches the `edges` table's `source_name`/`target_name` string columns. A consequence is that if two different files both define a symbol named `helper`, detection treats them as a single graph node. Both `symbols` rows receive the same `cluster_id` after write-back (`UPDATE symbols SET cluster_id=? WHERE name=?`). This is a known, accepted limitation of the name-based graph model: it is consistent with how edges are stored (they reference names, not row ids) and avoids re-engineering the entire edges schema. The practical impact is small — most same-name symbols across files belong to the same logical area anyway (e.g., multiple `__init__` or `validate` functions). Operators can observe the effect in `clusters.size` (which now counts actual DB rows, not unique names).

## Rejected alternatives

- **networkx/igraph:** Would require a new runtime dependency (rejected: "zero external services")
- **Leiden refinement step:** More accurate but significantly more complex; deferred to a future ADR
- **Per-file clustering:** Produces fragmented clusters that miss cross-file functional areas
- **In-watcher recompute:** Watcher is per-file; clustering is whole-graph. Documented staleness is acceptable (mirrors git's index model)

## Update to ADR-003

ADR-003 (naming layer design) noted the optional LLM naming layer as planned.
This ADR documents it as implemented and opt-in via config.
