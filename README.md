# Seam

Seam is a local code-intelligence index for coding agents: it turns a repository into a queryable symbol graph, available through a CLI or an MCP server.

## Why use it?

A coding agent often has to search files, open each match, and reconstruct relationships before it can answer a question like "what might be affected if I change this function?" Seam parses the code once, stores symbols and typed relationships in `.seam/seam.db`, and lets the agent ask about callers, paths and change impact directly. It does not replace reading the code: static analysis has limits, and an agent should inspect the relevant source before editing.

Queries run against the local index, without a network call. After edits, run `seam sync` to bring the index up to date. The optional MCP server starts a file watcher; graph results also flag a stale index.

## Quickstart

Requires Python 3.12 or newer.

```bash
pip install seam-code
cd /path/to/your/project
seam init
seam impact some_function
```

The package is named `seam-code` on PyPI; its command is `seam`. `seam init` writes a local index to `.seam/seam.db`. The CLI does not need a running server.

### One real example

This repository defines `init_db` in `seam/indexer/db.py`. From a checkout of Seam, after installing the CLI:

```bash
cd seam
seam init
seam impact init_db
```

`seam impact` traverses the indexed graph to show upstream dependents grouped by distance/risk tier. Treat it as an inspection lead, not proof that every listed caller will break or that unlisted dynamic behavior is safe. To look closer, run `seam context init_db` and inspect the relevant code. If the checkout changes, run `seam sync` before relying on impact results.

## Use it with an agent

`seam install` writes CLI guidance for Claude Code by default. Preview before writing, or choose another supported target:

```bash
seam install --print-config
seam install --target all
```

For native MCP tool calls, install the server extra and opt into MCP wiring:

```bash
pip install 'seam-code[server]'
seam install --with-mcp
```

This adds the stdio MCP server alongside the CLI guidance. Run `seam uninstall` to reverse the generated guidance and MCP configuration. See the installer preview for the exact files it will change.

## What it can answer

- **Find and understand code:** `seam search "auth token"`, `seam query "verify user login"`, `seam context some_function`, `seam snippet`.
- **Plan a change:** `seam impact some_function`, `seam plan some_function`, `seam changes`, `seam affected`.
- **Explore structure:** `seam trace`, `seam flows`, `seam clusters`, `seam architecture`, `seam graph-search`.

The CLI reads the index directly; the optional MCP server exposes 19 read-only tools over stdio. Seam parses Python, TypeScript, JavaScript, Go, Rust, Java, C#, Ruby, C, C++, PHP and Swift. See [concepts](docs/CONCEPTS.md) for edge types, confidence and language-specific caveats. Dynamic calls and unresolved names can be missed or ambiguous.

Optional extras: `seam-code[semantic]` adds local embeddings for hybrid search (a model download is needed on first use); `seam-code[web]` adds the local browser explorer. The base CLI does not require either. Advanced features, shared indexes, benchmarks and architecture are covered in [configuration](docs/CONFIGURATION.md), [benchmarks](docs/benchmark.md) and [architecture](docs/ARCHITECTURE.md).

MIT licensed. See [LICENSE](LICENSE).
