# Seam

Local code intelligence MCP server for AI agents. Index your codebase once; let agents query instead of grep.

## Status

Phase 0 — under development.

## Quickstart

```bash
# Install
pip install seam  # or: uvx seam

# Index your project
cd /path/to/your/project
seam init

# Start the MCP server
seam start
```

Add to your Claude Code MCP config:
```json
{
  "mcpServers": {
    "seam": {
      "command": "seam",
      "args": ["start", "--stdio"]
    }
  }
}
```

## MCP Tools (Phase 0)

- `seam_query(concept)` — find all code related to a concept
- `seam_context(symbol)` — get callers, callees, location, docstring
- `seam_search(text)` — full-text search across all symbols

## Development

```bash
uv sync --dev   # install deps
make gate       # run lint + typecheck + tests
```

See [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) for build status.
