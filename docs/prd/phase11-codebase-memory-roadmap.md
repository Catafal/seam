# PRD — Phase 11: Codebase-Memory-Inspired Status Matrix

> Status: tracking — updated 2026-07-03.
> Original source audit: `DeusData/codebase-memory-mcp` at commit `4a42285`.
> Refresh audit: `DeusData/codebase-memory-mcp` at commit
> `9cb3cabf76f5f4ad23caf66f641adff1ef0b67c9`.
> Current Seam snapshot: schema v14, Seam `0.4.0`, fresh index after `uv run seam sync`.

## Why This Document Changed

The first Phase 11 roadmap was written when `codebase-memory-mcp` exposed several
capabilities Seam did not yet have: schema introspection, snippet retrieval,
structural graph search, architecture summaries, route/config/test/exception
edges, a 3D graph UI, distribution hardening, and no-egress/install-scope checks.

That is no longer the current state. Seam has absorbed most of the useful core
ideas, so this document is now a status matrix rather than a forward-only phase
plan. The purpose is to keep the competitive research useful without pretending
shipped work is still proposed.

The product boundary remains the same: use `codebase-memory-mcp` as research,
not as code to port wholesale. Seam should keep its local-first, SQLite-backed,
read-only MCP, small-module, transport-neutral design.

## Current Snapshot

`uv run seam schema --json` reports the following capabilities in this repo:

| Surface | Current evidence |
|---|---|
| Indexed files | 446 |
| Symbols | 7,554 |
| Edges | 42,697 |
| Routes | 14 |
| Config keys | 109 |
| Resources | 27 |
| Test edges | 3,545 |
| Exception edges | 82 `raises`, 167 `catches` |
| MCP tools | 16, including `seam_schema`, `seam_snippet`, `seam_graph_search`, `seam_architecture` |
| Embeddings | Not populated in this repo snapshot |

## Status Matrix

### Shipped

These items should be treated as implemented Phase 11 work. Future work here
should be quality, contract coherence, and regression coverage, not initial
build-out.

| Item | Current state | Evidence | Next action |
|---|---|---|---|
| `seam_schema` | Shipped across CLI/MCP/Web. Reports freshness, counts, optional tables, capabilities, warnings, and tool guidance. | `seam/query/schema.py`, `seam/cli/schema.py`, `seam/server/tools.py`, `docs/api-contracts/mcp-tools.yaml`, `tests/unit/test_schema_tool.py` | Keep schema, architecture, and graph-search capability language aligned. |
| `seam_snippet` | Shipped exact bounded source retrieval by UID/symbol/file+line, including ambiguity handling and root containment. | `seam/query/snippet.py`, `seam/cli/snippet.py`, `tests/unit/test_snippet_tool.py` | Continue using as the source panel primitive in Explorer. |
| Structural graph search | Shipped typed search with kind, edge kind, degree, path, confidence, synthesized, preset, pagination, and previews. | `seam/query/graph_search.py`, `seam/cli/graph_search.py`, `tests/unit/test_graph_search.py` | Prefer extending typed filters over adding a general Cypher parser. |
| `seam_architecture` | Shipped bounded architecture summary with sections, scope, byte budget, routes/config/resources/exceptions/tests, hotspots, boundaries, next calls. | `seam/query/architecture.py`, `seam/cli/architecture.py`, `tests/unit/test_architecture_tool.py` | Fixed stale `NO_TEST_EDGES` warning on 2026-07-03; keep warnings tied to actual evidence. |
| Route nodes and route summaries | Shipped for supported Python/TypeScript route forms. | `seam/indexer/routes.py`, `tests/unit/test_routes.py` | `http_calls` coverage is still partial; expand carefully under protocol-edge work. |
| Config/resource nodes | Shipped key/resource extraction and architecture/schema reporting. | `seam/indexer/config_resources.py`, `tests/unit/test_config_resources.py` | Keep value handling conservative: keys/resources only, not secret values. |
| Test edges | Shipped static test-to-production evidence with provenance. | `seam/indexer/test_edges.py`, `tests/unit/test_test_edges.py`, `tests/unit/test_architecture_tool.py` | Use for architecture/test-impact confidence without making production impact noisier. |
| Raises/catches edges | Shipped conservative exception extraction. | `seam/indexer/exceptions.py`, architecture exception section, schema edge counts | Keep builtin/common exception modeling conservative. |
| 3D constellation | Shipped as a secondary Explorer topology surface with React Three Fiber, node cloud, additive edges, labels, tooltip, selection, filters, HUD, and `/api/constellation`. | `web/src/components/ConstellationScene.tsx`, `web/src/components/ConstellationTab.tsx`, `seam/server/web.py` | Add Playwright screenshot/canvas-pixel QA; keep 3D secondary to 2D navigation. |
| 2D Explorer upgrades | Shipped or superseded by the Phase 11 Explorer redesign stream. | `docs/prd/phase11-explorer-redesign.md`, Phase A/B/C/D PRDs and UI components | Treat `phase11-explorer-redesign.md` as the active UI roadmap. |
| npm shim | Shipped as a thin pinned `uvx --from seam-code==<version> seam ...` wrapper. | `pkg/npm/bin.js`, `pkg/npm/lib/invocation.js`, `tests/integration/test_npm_shim.py` | Keep this fail-loud and simple; do not copy downloader-heavy postinstall behavior. |
| Installer write-scope audit | Shipped fake-home/fake-root tests for install/uninstall/preview write boundaries. | `tests/integration/test_installer_write_scope.py`, `tests/unit/test_fs_audit.py` | Add new targets one at a time with matching write-scope tests. |
| No-egress proof | Shipped CI audit for normal local read paths. | `.github/workflows/no-egress.yml`, `tests/support/egress_audit.py` | Keep semantic model downloads explicitly outside read-path no-egress claims. |
| Diagnostics and soak | Shipped opt-in local diagnostics and mixed-query soak tooling. | `seam/analysis/diagnostics.py`, `benchmarks/soak.py` | Keep disabled by default and avoid source/secret capture. |
| Release hardening foundation | Shipped in part: release smoke, pinned-action audit support, npm/PyPI version lockstep. | `.github/workflows/release.yml`, `tests/support/actions_pin_audit.py`, `tests/unit/test_smoke.py` | Continue improving provenance/checksum publication. |

### Partially Shipped

These are real surfaces in Seam, but the implementation should not be considered
complete relative to the refreshed `codebase-memory-mcp` audit.

| Item | Current state | Gap | Next action |
|---|---|---|---|
| `http_calls` / cross-service route matching | Route nodes exist and TS literal HTTP-call tests exist, but this repo snapshot reports `has_http_calls: false`. | Static HTTP call extraction is not broad enough to rely on as an architectural boundary. | Expand only with confidence/provenance and graph-search/schema visibility. |
| 3D visual acceptance | The 3D surface exists and has unit/component coverage. | No browser screenshot or canvas pixel checks prove nonblank, legible rendering. | Add Playwright visual QA before treating Topology as release-polished. |
| Semantic search | Optional embedding infrastructure exists, but this repo has no embeddings populated. | No always-on local semantic edge story; semantic behavior must stay explicit about retrieval mode and fallback. | Keep semantic opt-in; do not introduce ambiguous `semantic_query` fallback behavior. |
| Release trust | Release workflow and audits exist. | No complete signed artifact/checksum/provenance story for every install path. | Add fail-closed verification for any future binary/artifact downloader. |
| Installer target breadth | Multiple agent targets are supported or planned. | Broad auto-detect write-everywhere behavior remains intentionally absent. | Add explicit targets only after preview/install/uninstall tests exist. |

### Still Useful

These ideas remain valuable, but they should be implemented as scoped follow-up
work rather than as unfinished Phase 11 core.

| Idea | Why it is still useful | Guardrail |
|---|---|---|
| Infra-as-code graph resources | `codebase-memory-mcp` indexes Docker, Kubernetes, Kustomize, and config-derived service bindings. This would improve deployment/debug questions in Seam. | Do not index secret values. Model declarations and references with provenance. |
| Protocol-edge expansion | gRPC, GraphQL, tRPC, channels, pub/sub, and event emitters are high-signal service boundaries. | Add one protocol family at a time; keep confidence and provenance visible. |
| Explicit graph artifact export/import | Team bootstrap from a compressed graph can save indexing time on large repos. | Must be explicit commands only: no automatic repo writes, import refuses newer schemas, metadata includes schema/version/root fingerprint. |
| SQLite/indexing performance benchmarks | The external project treats write lifecycle as a product surface. Seam should benchmark WAL, transactions, cache, and checkpoint behavior. | Accept speedups only when crash/recovery and stale-read behavior are proven safe. |
| Broader package-manager distribution | Homebrew/Scoop/Winget/etc. can reduce setup friction. | Package manifests should pin hashes and avoid hidden config mutation. |
| Local diagnostics/soak expansion | Useful for watcher leaks, slow queries, and long-running MCP sessions. | Keep opt-in, local-only, and source-text-free. |

### Needs RFC

These change scope, trust boundaries, or graph semantics enough that they need a
separate design before implementation.

| Candidate | Why it needs an RFC | Minimum questions to answer |
|---|---|---|
| Cross-repo intelligence | Cross-repo `CROSS_*` edges and multi-galaxy summaries are compelling, but Seam is currently per-repo. | How are permissions, project selection, freshness, and accidental cross-repo exposure handled? |
| Resolved edge IDs | ID-backed edges can improve homonym precision, but Seam's current name-keyed edges support independent per-file re-indexing. | Additive table or nullable endpoint columns? How does watcher correctness survive changed endpoint files? |
| Semantic/similarity edges | Discovery can improve, but semantic edges can pollute impact/risk traversal if treated like dependencies. | Which surfaces consume semantic edges? How are they excluded from impact by default? What precision/recall gate is required? |
| Runtime trace ingest | Runtime traces could validate dynamic routes and generated paths. | What trace source first? What structural metadata is stored? How are secrets/request data scrubbed? |
| Full query language / Cypher | Arbitrary graph queries help experts but increase parser, safety, pagination, and compatibility cost. | What recurring user questions cannot be expressed by typed tools? What hard cost/read-only budget is enforced? |
| Broad auto-detect installer | It can reduce setup friction after many targets exist. | How does the default stay preview-only? How does per-target consent and owned-block uninstall work? |

### Rejected

These should stay out unless the product direction changes materially.

| Rejected item | Reason |
|---|---|
| Porting the C codebase or vendored parser strategy | Seam's bottleneck is answer quality, graph semantics, and adoption, not an urgent need to rewrite in C. Python keeps modules smaller and easier for agents/maintainers to patch. |
| Startup update checks | Automatic network calls undermine Seam's local-first/no-egress trust story. Release discovery belongs in explicit user commands or documentation. |
| Write/delete/index/ADR mutation tools through default MCP | Seam's MCP surface should stay read-only by default. Mutation is a permission boundary and belongs in explicit CLI/UI flows. |
| Broad auto-detect-and-write installer as default | Installation writes to personal agent/editor configs. Seam should keep explicit, previewable, reversible targets. |
| Fail-open binary integrity checks | The refreshed audit found multiple external installer/update paths that continue when checksum verification is missing. Seam should fail closed wherever it claims verification. |
| Unauthenticated privileged localhost UI endpoints | CORS is not an authorization boundary for local processes. Privileged UI actions need a token or should stay behind explicit stdio/CLI flows. |
| Ambiguous semantic fallback contracts | A tool named semantic must not silently return generic lexical/graph results without clear retrieval-mode metadata. |

## Current Follow-Up Order

1. **Coherence pass for shipped Phase 11 tools.**
   - Keep `seam_schema`, `seam_architecture`, `seam_graph_search`, and `seam_snippet`
     capability language aligned.
   - Prevent warnings from contradicting populated evidence.
   - Keep CLI/MCP/Web contracts in sync.

2. **Explorer visual QA.**
   - Add Playwright screenshots and canvas-pixel checks for the Topology/3D path.
   - Verify desktop and mobile nonblank rendering.
   - Keep 3D off the critical navigation path.

3. **Release trust hardening.**
   - Continue pinned-action checks and release smoke installs.
   - Add checksums/provenance where practical.
   - Keep npm as a pinned `uvx` wrapper unless a fail-closed binary artifact story exists.

4. **Protocol and infra graph RFCs.**
   - Start with HTTP call extraction quality, then infra-as-code resources.
   - Defer broader protocol families until confidence/provenance conventions are stable.

5. **Artifact export/import RFC.**
   - Design explicit export/import commands with metadata and schema safety.
   - Keep artifacts out of the default repo workflow.

## Open Questions

1. Should `NO_TEST_EDGES` warn only when zero `tests` edges exist, or should a schema
   version with support but no matching tests suppress it? Current behavior is evidence-based:
   populated `tests` edges suppress the warning.
2. Should `http_calls` be promoted as its own mini-phase before broader protocol edges?
3. Should graph artifact export/import be optimized for local team bootstrap first, or CI cache
   bootstrap first?
4. Which package-manager target has enough user demand to justify the next explicit installer
   target?

## Success Criteria

- Phase 11 docs show what is shipped versus what still needs design.
- Agents can use schema, snippet, graph-search, architecture, and test-edge evidence without
  contradictory warnings.
- Explorer keeps 2D as the precision workflow and 3D as an honest topology surface.
- Any future distribution improvements preserve Seam's trust claims: local, reversible,
  bounded output, no unexpected network calls, and no hidden repo mutations.
