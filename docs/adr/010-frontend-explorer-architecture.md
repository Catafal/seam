# ADR-010: Frontend Explorer — Python-served, bundled TypeScript SPA

## Status
Accepted — 2026-06-03

## Context
Seam shipped `seam-mcp 0.1.0` with no human-facing UI: reads happen through the
`engine.query/context/search` functions over SQLite, wrapped by transport-agnostic
`handle_seam_*` handlers, exposed via the CLI (`--json`) and the MCP server (stdio).
A browser can speak neither stdio nor SQLite directly without re-implementing query logic.

We want a visual graph explorer (an "OpenAI-style" card-canvas of symbols, edges, and
clusters). The author prefers TypeScript for UI work, which raised a genuine fork: build
a TS frontend on top of the existing Python engine, or rewrite the whole engine in TS so
frontend and backend share one stack.

ADR-001 ("Python over TypeScript") governs the *engine* and explicitly anticipates this:
"If an npm package is ever needed (CLI wrapper, IDE extension): use @catafal/seam." A TS
*frontend* does not conflict with it.

## Decision
**The Combination: keep the Python engine; add a TypeScript frontend on top.**

1. **`seam serve`** — a new, OPTIONAL FastAPI server bound to `127.0.0.1` only. It is a
   *third transport* over the existing `handle_seam_*` handlers (alongside CLI and MCP),
   plus new read-only graph endpoints. Zero query-logic duplication. Read-only. No auth
   (localhost-only is the boundary).
2. **TypeScript SPA** — Vite + React + TypeScript + **React Flow**. The hero view is a
   *neighborhood card-canvas*: search → land on a node → see depth-1 callers/callees/peers
   as cards → click to expand. Layout is computed client-side (dagre/elkjs); the API
   returns topology + enrichment only, never coordinates.
3. **Node = name.** The explorer graph is keyed on symbol name, identical to the engine's
   own edge graph; homonyms collapse to one node (see CONTEXT.md). This keeps what the UI
   draws byte-consistent with what `seam_impact`/`seam_trace` traverse.
4. **Distribution** — the SPA lives in `web/` (monorepo), builds to `seam/_web/`, and is
   force-included into the wheel exactly like `schema.sql`. `pip install 'seam-mcp[web]'`
   ships the UI. Node.js is a build-time dependency only; never a runtime requirement.
5. **Typed contract** — FastAPI's OpenAPI schema is codegen'd to TS types
   (`openapi-typescript`), so Pydantic response models are the single source of truth for
   the client's types.

Prior art: `datasette` (Python + SQLite + a bundled localhost web UI, single `pip install`).
This is "datasette for code graphs."

## Alternatives Rejected
- **Full TypeScript rewrite.** Throws away 1504 passing tests and the just-shipped PyPI
  package; forces re-implementing tree-sitter extraction for all 12 languages, Louvain
  clustering, and the confidence/import-promotion resolver in TS — weeks-to-months for
  zero UI benefit. UI smoothness is bounded by browser render cost, not by backend
  language. npm `seam` is permanently blocked anyway. Justified only for a future
  browser-only hosted product (contradicts Seam's local identity) or a pure-npm IDE
  extension (which would wrap the engine, not rewrite it).
- **TS reads SQLite directly** (better-sqlite3/sql.js). Re-implements FTS rescore,
  confidence resolution, and cluster reads in TypeScript → drift against the Python source
  of truth. Fastest to a demo, worst for correctness.
- **Static JSON export.** A frozen snapshot SPA — no live query/search; re-export after
  every reindex. Kept as a possible future "share a snapshot" feature, not the explorer.
- **Server-side graph layout.** Returning node coordinates from Python welds a rendering
  decision into the backend — wrong layer. Layout stays in the view.
- **Node per definition** `(file, name)`. Edges are name-keyed, so a per-definition node
  cannot know which definition an edge targets; you would fan every edge to all same-name
  definitions — visual explosion plus implied precision the index does not have.

## Consequences
- New optional `[web]` extra. `starlette`, `uvicorn`, `pydantic`, `anyio`, `httpx` are
  ALREADY transitive via the `mcp` (`[server]`) extra, so the only genuinely new runtime
  package is `fastapi` itself.
- Release ritual gains one step: `make build-web` (Vite build) → `uv build` → `uv publish`.
  `seam/_web/` is gitignored (build artifact, not source).
- The MCP server stays read-only and unchanged; tool count stays 10. `seam serve` is
  CLI-only — there is no `seam_serve` MCP tool.
- The explorer inherits the homonym-collapse behavior *by design*; the detail panel lists
  all definitions behind a node so nothing is hidden.
- Deferred to phase 2: the whole-repo **constellation** overview (a second renderer —
  Cytoscape/Sigma), the **impact overlay**, **trace** path view, and a **git-changes** view.
- `127.0.0.1`-only binding preserves the "zero external services at runtime" non-negotiable:
  nothing leaves the machine.
