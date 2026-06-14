# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
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
