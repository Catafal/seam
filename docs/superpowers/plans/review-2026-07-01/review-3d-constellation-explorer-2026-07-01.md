# Plan Review Report

## Plan: 2026-07-01-3d-constellation-explorer.md
## Mode: Autonomous (2 parallel reviewers: Structure+Perf / Quality+Tests)
## Date: 2026-07-01
## Seam: available (repo self-indexed)

## Executive Summary

The plan's **architecture is sound** — purely additive leaf module + sibling route module
(correct `register_*_routes` pattern) + lazy tab. But **Tasks 1 and 2 would not run as written**,
and one defect makes the output **wrong on real data**. Both reviewers independently flagged the
connection-handling bug. Six critical fixes are required before implementation; all are cheap and
localized. Confidence after fixes: high.

## Critical Issues — Block Implementation

**CR1 — Qualified↔bare name asymmetry breaks the graph (correctness, not just tests).**
`compute_layout` keys degree/adjacency on `symbols.name` (`"Client.send"`), but `edges` store the
**bare** target (`"send"`). Seam's core asymmetry — `seam/query/names.py` exists to bridge it. As
written, every method gets **degree 0** and renders as an isolated star; the constellation is
visibly broken on any real repo. **Fix:** build degree/adjacency through
`names.edge_match_names(...)` / `bare_name(...)` (reuse the existing leaf). Add a fixture edge to a
qualified member asserting non-zero degree. [PREF_DRY, PREF_EDGE_CASES]

**CR2 — `_get_readonly_conn` misuse: `str()` 500s and `with` leaks (both reviewers).**
`open_readonly_connection(db_path: Path)` takes a **Path** (calls `.resolve()`) and returns a
**plain connection, not a context manager**. The plan's `with open_readonly_connection(str(db_path))`
→ `AttributeError` (500) on every call, and `with conn:` never closes → FD leak, and loses the
`NO_INDEX`/`DB_ERROR` 503 contract. **Fix:** copy the `_get_readonly_conn(db_path)` helper pattern
from `web_graph_search.py:16-28` + `try/finally: conn.close()`; add a no-index → 503 test.
[PREF_EXPLICIT, PREF_DRY]

**CR3 — Test fixture won't build a schema or insert rows.** Two independent failures:
`init_db(db_path: Path) -> Connection` takes a **path and returns** a conn (plan did
`connect(str); init_db(conn)` → schema applied to a junk DB, original conn has no tables); and the
`files` INSERT uses non-existent `sha1` and omits NOT NULL `language`/`file_hash`. **Fix:**
`conn = init_db(tmp_path / "seam.db")`; `INSERT INTO files(path, language, file_hash, mtime, indexed_at)`.
[PREF_EXPLICIT, PREF_TESTS]

**CR4 — No caching implemented, but the spec requires it and Task 8 documents it as existing.**
`compute_layout` runs the full O(n²)·40-iter kernel (1-3s @2k) on **every** request; react-query
`staleTime` only dedupes one client. Task 8 would write a CLAUDE.md gotcha describing a cache that
doesn't exist. **Fix:** module-level bounded dict in `layout.py` keyed on
`(MAX(indexed_at), max_nodes)` (self-contained — one cheap query, no signature change), TTL =
`SEAM_STALENESS_TTL_SECONDS`. [PREF_ENGINEER]

**CR5 — numpy O(n²) memory OOMs at the endpoint's `le=10000` (~4.8 GB).** Default 2000 is safe
(~300 MB) but the endpoint accepts up to 10000. **Fix:** lower `le` to a config-driven safe ceiling
(`SEAM_LAYOUT_MAX_SAFE_NODES`, default 3000) AND clamp inside `compute_layout` so the module is safe
regardless of caller. Document 5000+ (chunked/kNN) as a follow-on. [PREF_ENGINEER, PREF_EDGE_CASES]

**CR6 — Config-knob convention (non-negotiable).** `max_nodes` default + safe ceiling + cache TTL
are hardcoded; CLAUDE.md mandates config only via `seam/config.py`. **Fix:** add
`SEAM_LAYOUT_MAX_NODES` (2000), `SEAM_LAYOUT_MAX_SAFE_NODES` (3000); reuse `SEAM_STALENESS_TTL_SECONDS`.
Algorithm constants (`_REPULSION`, `_ITERATIONS`…) stay module-local (matches `clustering.py`/`rwr.py`
leaf discipline). [PREF_EXPLICIT]

## Important Issues — Address During Implementation

**IM1 — Thin edge-case tests.** Add: NULL `cluster_id` node; self-edge (`s==t`) rejection;
capped-edge filtering (max_nodes=2 → no edge references a dropped id); homonym collapse (two
`helper` symbols → one node, min-id `file_path`); single-member cluster radius fallback
(`60.0*1.2`). [PREF_EDGE_CASES, PREF_TESTS]

**IM2 — Extract pure logic out of R3F components for real coverage.** Currently ~0% of render
logic is tested. Extract + unit-test: `buildEdgeGeometry(nodes, edges, highlightedIds)` (highest
value — index-mapping bugs), `computeInstanceColor(node, hi, dim)` (boost math),
`easeOutCubic(p)`, `selectLabelNodes(nodes, cap)`. Each is ~3 lines + ~3-line test.
[PREF_TESTS (more>fewer), PREF_ENGINEER]

**IM3 — Narrow the blanket `except Exception`.** It masks numpy/`KeyError`/`ValueError` bugs as an
empty layout indistinguishable from an empty index. Narrow to
`except (sqlite3.Error, ValueError, KeyError)` (matches graph_api's `sqlite3.Error` discipline) +
a malformed-row test (e.g. NULL `name`) asserting graceful empty. [PREF_EXPLICIT, PREF_EDGE_CASES]

**IM4 — react-query error/loading path unspecified/untested.** A 500 yields a silent blank canvas.
Add `isError`/`isLoading` branches in `ConstellationTab` + a vitest mocking `fetch → 500`.
[PREF_TESTS, PREF_EXPLICIT]

**IM5 — DRY vs `graph_api.py`.** Name-representative collapse, degree (`UNION ALL … GROUP BY name`),
and the name→cluster map already exist there. The CR1 fix already pulls in `names.py`; cross-ref
`graph_api.py` in a comment and reuse its degree SQL rather than hand-rolling. Positions (numpy) are
genuinely new and stay in `layout.py`. Full helper-extraction refactor is out of scope. [PREF_DRY]

**IM6 — Staleness banner: decide explicitly.** Spec says attach `index_status`; plan omits it.
**Resolution:** intentional omission — the HUD freshness dot (`/api/status`) already covers the
user-facing need for a cosmetic surface. Record this as an explicit decision in the plan, not a
silent gap. [PREF_ENGINEER]

## Minor Issues — Track

- **MI1** TS `GraphNode`/`GraphEdge` collide with the existing 2D `graph_api` `GraphNode`
  (different shape). Rename TS types → `LayoutNode`/`LayoutEdge` (mirror the backend). [PREF_EXPLICIT]
- **MI2** Add one golden-coordinate assertion (rounded) so an accidental algorithm change is caught,
  not just re-run determinism. [PREF_TESTS]
- **MI3** `_bfs_depth` uses `list_entry_points(conn)` with no limit → capped at
  `SEAM_FLOW_ENTRY_LIMIT` (20); on large repos only 20 roots seed z-depth. Pass a larger limit or
  comment the intentional cap. [PREF_EXPLICIT]
- **MI4** `_compute_layout_impl` mutates the borrowed `conn.row_factory` — set locally / restore.
  Harmless on the readonly path. [PREF_EXPLICIT]
- **MI5** Verified OK (no action): `register_*_routes(app, *, db_path, root)` wiring, `create_web_app`
  call site, `list_entry_points` `TypedDict` `.get("name")`, all field-name/color consistency,
  `stellar_color`/`node_size` test expectations, Vite lazy code-split mechanism.

## Statistics
- Total unique issues after dedup: 17 (6 critical, 6 important, 5 minor)
- Both reviewers independently flagged the connection bug (CR2) and the max_nodes ceiling (CR5).
- Review agents: 2 (Structure+Perf, Quality+Tests). Seam: available.

## Recommended Plan Updates
Apply CR1–CR6 as inline corrections to Tasks 1–3 (fixture, connection handling, caching, name
bridging, config knobs, safe ceiling). Fold IM1–IM4 into their tasks' test steps. Record IM6 as an
explicit decision. Apply MI1 (rename) before Task 3. The plan's task structure, ordering, and
altitude are otherwise sound — no re-decomposition needed.
