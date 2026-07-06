# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.5.0] - 2026-07-06

A large additive release. Every feature below is backward-compatible: existing
behavior is byte-identical when new features are left at their (off/default)
settings, there are no breaking schema migrations, and no MCP tool was removed.
The MCP tool count grew from **12 to 16**.

### Added
- **Four new MCP tools (Phase 11 P1):** `seam_schema` (index capability + schema
  introspection), `seam_snippet` (exact bounded source retrieval by symbol/UID/file+line),
  `seam_graph_search` (typed structural graph search with UID chaining + paginated filters),
  and `seam_architecture` (bounded repository architecture briefing). Tool count 12 → 16.
- **Semantic search overhaul (WS1–WS3):** richer embedding input — a leading body slice +
  WHY/HACK/NOTE comments (`SEAM_EMBED_BODY`, WS1); a persisted mmap vector store that fixes a
  silent recall ceiling past the old scan cap and is zero-copy per query (`SEAM_VECTOR_STORE`,
  WS2a); an opt-in `sqlite-vec` KNN tier as a forward-compatible scaffold (`SEAM_VEC_ANN`, off
  by default, WS2b); and incremental re-embedding so `seam sync --semantic` only embeds new
  symbols (WS3). All opt-in; keyword-only indexes are byte-identical.
- **Richer code graph — five new edge kinds:** `holds` (typed stored composition), `uses`
  (method-parameter type coupling), `reads`/`writes` (field access, A3), and `instantiates`
  (Tier B), plus whole-graph edge synthesis (interface→impl override fan-out, closure-collection
  and event-emitter dynamic dispatch). Receiver-type inference now emits qualified `Type.method`
  call targets across all 12 languages. Blast-radius and change-risk verdicts are correspondingly
  more complete.
- **Protocol, config, and infra graph (Phase 11 P3 + infra):** HTTP call / route-node edges,
  config- and resource-link edges, exception/`raises` edges, static test edges, and a Docker
  Compose + Dockerfile infrastructure graph. Plus graph-artifact lifecycle, cross-repo workspace
  analysis, and hybrid exact receiver-type resolution.
- **Agent-workflow surfaces:** `seam_context_pack` relationship evidence + caveats + recommended
  next calls; an agent change-planning surface; conservative dead-code / orphan-suspect
  confidence; and docs/spec grounding.
- **`seam impact` output shaping:** per-tier `risk_summary` + count cap + `truncated`; relevance
  ranking of external dependents ahead of self-references (E2/E3); lossless omission of null
  `best_candidate` (E1); an opt-in hard byte ceiling (`SEAM_IMPACT_MAX_BYTES`, E1-FULL); edge
  provenance (`kind` + `synthesized_by`, E4); and actionable `next_actions` truncation hints.
  A read-path index-staleness banner (`index_status`) now surfaces on the five graph-traversal
  tools (P2). Personalized-PageRank neighbor ranking for `seam_context_pack` (E3).
- **WS6.1 — agent-trace-derived eval goldens:** an opt-in, local, offline trace-capture loop
  (`SEAM_TRACE_CAPTURE`) that records real read-path tool calls (symbols-only) and derives
  recall-golden candidates by correlating each query against the symbols the session actually
  edited (git-diff hindsight), for human promotion into a separate repo-keyed live golden set.
  Off by default = byte-identical; never touches the deterministic fixture goldens.
- **Seam Explorer (web UI, `[web]` extra):** a 3D symbol-graph Constellation, a coherent
  Overview / Symbol / Topology tab model with breadcrumbs and a server-admin status strip, a
  degree-sized/colored treemap, resizable panels, grouped clickable caller/callee rows with edge
  metadata, a file-tree sidebar, and package-plumbing exclusion from rankings. Served via
  `seam serve`.
- **Execution flows & structure:** `seam_flows` (entry points + forward call-chain trees) and
  `seam_structure` (whole-repo directory/container tree).
- **Team distribution & escape hatches:** `seam fetch` — download, verify, rebase, and sync a
  CI-prebuilt index artifact for fast onboarding (`seam pack-index` / `seam rebase`, WS4); and
  `--to-file` on heavy read commands to spill full results to `.seam/out/` instead of the context
  window (WS5).
- **Broader installer + hardening:** `seam install` now targets VS Code, Gemini CLI, and Zed in
  addition to Claude Code / Cursor / Codex (P6.4); SHA-pinned CI actions + OIDC Trusted Publishing
  (P5.2); an installer write-scope audit (P5.3); and a Linux-CI no-egress syscall proof that the
  read path makes zero external connections (P5.4).
- **Opt-in local diagnostics + soak testing (P5.5, issues #237–#242):** set
  `SEAM_DIAGNOSTICS=1` to append lightweight operational metrics — RSS, open-FD count, DB
  size, query count, slow-query summaries, and watcher counters — to a local append-only
  NDJSON file inside `.seam/` (already gitignored). Records ONLY tool names + numeric metrics;
  never source text, query arguments, or secret-like values (structural redaction, gate-tested).
  Disabled by default = byte-identical no-op (no file, no sampling, no atexit handler, unchanged
  MCP tool schema; tool count stays 16). Instruments the 16 MCP tools, the `seam
  search/query/context/impact/trace` CLI commands, and the file watcher. Local-file-only — no
  network, no telemetry, no new runtime dependency. New `benchmarks/soak.py` + `make soak` drive
  sustained mixed read load against an indexed repo and print a resource/latency summary (run
  `SEAM_DIAGNOSTICS=1 make soak` to also capture the NDJSON trace). Two knobs tune it:
  `SEAM_DIAGNOSTICS_PATH` (default `.seam/diagnostics.ndjson`) and `SEAM_DIAGNOSTICS_SLOW_MS`
  (default 100). `open_fds` is Linux-only (`null` elsewhere); `rss_bytes` is peak RSS.
- **npm shim `@catafal/seam` (P5.1, issue #229):** `npx @catafal/seam <cmd>` delegates to
  `uvx --from seam-code==<version> seam <cmd>`, letting JS/TS projects use Seam without a
  Python toolchain. Requires [uv](https://docs.astral.sh/uv/getting-started/installation/)
  at runtime; the shim contains no bundled binaries and no install-time scripts. npm and
  PyPI versions are locked in lockstep; `make gate` enforces parity via
  `test_npm_package_version_matches_pyproject`. Publish trailing the PyPI release:
  `cd pkg/npm && npm publish --access public`.
- Community-health scaffolding for open-source readiness: `CONTRIBUTING.md`,
  `CODE_OF_CONDUCT.md`, `SECURITY.md`, this `CHANGELOG.md`, GitHub issue/PR templates,
  and a CI workflow (`.github/workflows/ci.yml`) that mirrors `make gate` on Python 3.12/3.13.

## [0.3.0] - 2026-06-08

### Added
- Published to PyPI as **`seam-code`** (the `seam` distribution name belongs to an unrelated
  package; the import package and `seam` command keep the short name).
- CLI-first `seam install`: writes token-lean CLI guidance into an agent (Claude Code skill /
  Cursor `.mdc` rule / Codex `AGENTS.md` block); `--with-mcp` additionally writes the MCP config.
  `seam uninstall` reverses both.
- Index staleness banner (`index_status`) on the five graph-traversal tools, surfacing when the
  index is stale; `seam status` freshness is watcher- and synthesis-aware.

### Changed
- `seam install` defaults to CLI guidance; MCP wiring is now opt-in via `--with-mcp`.

## [0.2.1] - 2026-06-04

### Fixed
- Clean sdist — the 0.2.0 sdist shipped bloated artifacts (PyPI releases are immutable, so this
  was republished as 0.2.1).

## [0.2.0] - 2026-06-04

### Fixed
- `[web]` packaging: the sdist now ships the built SPA and declares `uvicorn`, so
  `seam serve` works from a clean install.
- Excluded the `web/` toolchain from the sdist (18 MB → 1 MB).

## [0.1.0] - 2026-06-03

### Added
- Initial release of Seam — a local code-intelligence MCP server.
- Tree-sitter indexing into SQLite + FTS5; 12 languages.
- MCP tools: `seam_query`, `seam_context`, `seam_search`, `seam_impact`, `seam_trace`,
  `seam_changes`, `seam_why`, `seam_clusters`, `seam_affected`, `seam_context_pack`,
  `seam_flows`, `seam_structure`.
- CLI: `init`, `sync`, `start`, `status`, `query`, `search`, `context`, `impact`, `trace`,
  `changes`, `why`, `clusters`, `affected`, `pack`, `flows`, `structure`, `install`, `serve`.
- Optional extras: `[server]` (MCP), `[semantic]` (embedding search), `[web]` (Explorer UI).

[Unreleased]: https://github.com/Catafal/seam/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/Catafal/seam/compare/v0.2.1...v0.3.0
[0.2.1]: https://github.com/Catafal/seam/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/Catafal/seam/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Catafal/seam/releases/tag/v0.1.0
