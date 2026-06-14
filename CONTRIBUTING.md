# Contributing to Seam

Thanks for your interest in Seam — a local code-intelligence MCP server that indexes
codebases with tree-sitter and lets AI agents *query* instead of *grep*. Contributions
of all kinds are welcome: bug reports, language extractors, new tools, docs, and tests.

## Ground rules

By participating you agree to abide by our [Code of Conduct](CODE_OF_CONDUCT.md).

## Development setup

Seam uses [uv](https://github.com/astral-sh/uv) for environment and dependency management.

```bash
# Clone
git clone https://github.com/Catafal/seam.git
cd seam

# Install all dev dependencies (ruff, mypy, pytest, plus mcp + fastapi for integration tests)
make install-dev      # == uv sync --dev

# Sanity check — index this repo and query it
uv run seam init
uv run seam search "tree-sitter parser"
```

Optional extras:

```bash
uv sync --extra server     # MCP server  (seam start)
uv sync --extra web        # Explorer UI (seam serve)
uv sync --extra semantic   # semantic search (downloads a ~67 MB model on first init --semantic)
```

## The gate — run it before every commit

There is **one** command you must keep green:

```bash
make gate    # ruff (lint) + mypy (typecheck) + pytest (tests)
```

This is the same check CI runs on your PR. A red gate will block the merge.

- `make fmt` — auto-format and fix lint (run this first if `make lint` complains)
- `make test` — tests only
- `make lint` / `make typecheck` — individual stages

**Never use `git commit --no-verify`** to bypass the gate.

## Project conventions (non-negotiables)

These keep Seam fast, offline, and dependency-light. PRs that violate them won't merge.

- **Zero external services at runtime** — no API keys, no network calls. Everything is local.
- **SQLite only** — no Neo4j, no graph DB, no ORM. FTS5 is built in.
- **Config lives in `seam/config.py`** — never call `os.getenv()` from other modules; add a
  knob with a sensible default instead.
- **Parsers never raise** — return `None` on error so the indexer skips the file gracefully.
- **Edges use string names** (not symbol IDs) — required so files can be re-indexed independently.
- **Type hints required** — use `X | None`, not `Optional[X]`.
- **Size caps** — max 200 lines per function, max 1000 lines per file. Split into a leaf
  module before you hit the ceiling.
- **Imports at the top** of the file.
- **Tests mirror the package** under `tests/`.
- Naming: `snake_case` files & functions, `PascalCase` classes, `UPPER_SNAKE` constants.

See [`CLAUDE.md`](CLAUDE.md) and [`BACKEND_STRUCTURE.md`](BACKEND_STRUCTURE.md) for the full
module map and import rules.

## Adding a language

Seam supports 12 languages. To add another:

1. Add the tree-sitter grammar to `pyproject.toml` and the extension → language entry in
   `SEAM_LANGUAGE_MAP` (`seam/config.py`).
2. Write a per-family extractor module (`seam/indexer/graph_<lang>.py`) mirroring
   `graph_go_rust.py` — symbols, edges, and comments. Import `graph_common` only.
3. Wire enrichment in `signatures_ext.py` and import-mapping in `imports_ext.py`.
4. Add fixtures under `tests/fixtures/` and tests that prove symbols + edges extract.
5. Confirm the grammar parses cleanly (`has_error == False`) on a realistic file before
   committing — see ADR-009 (Kotlin) for why a noisy grammar is a no-go.

## Pull request process

1. Fork and branch off `main` (`feat/…`, `fix/…`, `docs/…`).
2. Keep PRs focused — one logical change.
3. Add or update tests for any behavior change.
4. Update docs (README, `docs/`, and `CHANGELOG.md` under `## [Unreleased]`).
5. Run `make gate` — it must pass.
6. Open the PR using the template; describe the change and link any issue.

## Reporting bugs / requesting features

Use the issue templates. For bugs, include the Seam version (`seam --version` /
`pip show seam-code`), Python version, OS, and a minimal repro.

## Security

Please do **not** open public issues for security problems — see [SECURITY.md](SECURITY.md).
