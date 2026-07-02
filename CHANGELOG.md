# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
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
