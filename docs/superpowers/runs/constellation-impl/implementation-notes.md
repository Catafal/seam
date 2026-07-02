# Constellation Explorer — Implementation Notes

## 2026-07-02 — Slice S1: Backend Layout Endpoint (issue #169)

### What was built

**seam/query/layout.py** — Deterministic 3D layout engine:
- `compute_layout(conn, *, max_nodes) -> LayoutResult` — never raises; module-level cache keyed on `(indexed_at * 1_000_000 + file_count, max_nodes)` with TTL from `SEAM_STALENESS_TTL_SECONDS`
- `stellar_color(degree) -> str` — degree → stellar hex color (M red dwarf → O blue giant)
- `node_size(kind, degree) -> float` — base size by kind + degree boost capped at +10
- `_force_atlas2(seed, mass, edges) -> np.ndarray` — 40-iteration numpy ForceAtlas2 O(n²)
- `_bfs_depth(conn, selected, sel_set, adjacency)` — BFS from `list_entry_points` for z-axis depth
- `_cluster_summaries(conn, selected, reps, name_to_idx, pos)` — cluster centroids, radii, colors

**seam/server/web_layout.py** — FastAPI route:
- `register_layout_routes(app, *, db_path, root)` — `GET /api/graph/layout?max_nodes=N`
- `LayoutResponse`, `LayoutNodeModel`, `LayoutEdgeModel`, `LayoutClusterModel` — Pydantic models (Layout* namespace to avoid 2D collision)
- 503 NO_INDEX / DB_ERROR via `_get_readonly_conn` (mirrors web_graph_search.py)

**seam/config.py** — 2 new knobs:
- `SEAM_LAYOUT_MAX_NODES = 2000` — default render cap
- `SEAM_LAYOUT_MAX_SAFE_NODES = 3000` — hard OOM ceiling (applied as first line of `_compute_layout_impl`)

**pyproject.toml** — `numpy>=1.26` added to `[web]` optional extra

**Tests** (16 tests total, all green):
- `tests/unit/test_layout.py` — 12 tests covering helpers + pipeline
- `tests/integration/test_web_layout.py` — 4 endpoint tests

### Key decisions

**CR1 — qualified↔bare bridge:** `edge_match_names(conn, name)` is called with `conn` as the first argument (the actual signature). Process **qualified (dotted) names FIRST** so `Client.send` claims the bare key "send" in `match_to_name` before the `Client` class expansion can steal it via `edge_match_names(conn, "Client")` returning `["Client", "send", ...]`. Without this ordering, `Client.send` would be an isolated star.

**CR4 — cache key uniqueness:** The plan suggested `(MAX(indexed_at), max_nodes)` but tests revealed that an empty DB and a non-empty DB both have `indexed_at=0` (test fixture sets it explicitly). Added `COUNT(*) FROM files` to the version: `ver = indexed_at * 1_000_000 + file_count`. This prevents the cache from returning a non-empty layout for an empty DB when tests share a process.

**Malformed-row test:** The plan suggested `UPDATE symbols SET name = NULL WHERE id = 1` but the schema has `name TEXT NOT NULL`. Instead tested the narrow-except path by closing the connection before calling `compute_layout` — a closed connection raises `sqlite3.ProgrammingError` (subclass of `sqlite3.Error`), which is exactly what the narrow except catches.

**web.py 1000-line cap:** Adding 2 lines to `web.py` pushed it to 1001. Removed 1 trailing blank line after the import block and 1 blank line between `register_layout_routes` and the next comment. File stays at 999 lines.

**`_compute_layout_impl` inner imports:** `from collections import deque` was moved to the module-level imports section (in `_bfs_depth` the collections import was used); all imports are at the top of the file as required.

**TypedDict → Pydantic coercion:** `LayoutResponse(**result)` caused mypy errors because `result` contains `list[LayoutNode]` (TypedDict) not `list[LayoutNodeModel]` (Pydantic). Fixed with `LayoutResponse.model_validate(result)` (Pydantic v2 API).

### Deviations from plan

- Cache key includes `COUNT(*) FROM files` (not in plan) to handle `indexed_at=0` collisions
- `edge_match_names` takes `conn` as first arg (plan's CR1 code was missing `conn`) — adapted
- Malformed-row test uses closed connection instead of NULL name (schema constraint)
- Applied qualified-before-plain iteration to fix the degree-bridge ordering issue

### Open questions

- None for S1. The layout engine is complete and all 3151 tests pass.
- S2 (frontend) should start from Task 3 in the plan.
