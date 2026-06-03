# Seam — Glossary

Canonical language for the project. Definitions only — no implementation detail.
When a term here conflicts with how a word is being used in conversation or code, the
conflict must be resolved before proceeding.

## Core domain (the index)

- **Symbol** — a named code entity extracted by the indexer (function, method, class,
  type, interface, etc.). Carries kind, signature, visibility, and a qualified name.
- **Edge** — a directed relationship between symbols (a call or an import). Keyed on
  symbol **name**, not on `(file, name)` — this is deliberate and has consequences (see
  *Node* below).
- **Cluster** — a functional area of the codebase: a community of symbols detected by
  Louvain over the edge graph, with a human-readable label.
- **Confidence tier** — how sure the index is that an edge resolves to the right target:
  `EXTRACTED` (resolved), `AMBIGUOUS` (name collision, best-guess), `INFERRED`
  (name-count only). A property of an edge, surfaced to the user.

## Explorer (the human-facing frontend)

- **Explorer** — the visual, browser-based graph UI for a Seam index. Read-only. Served
  locally; it does not replace the CLI or the MCP server, it sits alongside them.
- **Node** — in the Explorer's graph, a node is a unique symbol **name**. Because edges
  are name-keyed, two definitions that share a name (homonyms) collapse into one node —
  the same collapse the engine's own graph traversals use. A node may therefore map to
  more than one underlying *definition*.
- **Definition** — one concrete symbol row `(file, name)`. A *Node* may have several.
  The detail view lists every definition behind a node.
- **Card** — the visual representation of a node on the canvas.
- **Neighborhood** — a node plus its depth-1 graph relations (direct callers, direct
  callees, cluster peers). The Explorer grows the view one neighborhood at a time, on
  demand, rather than rendering the whole repo at once.
- **Constellation** — the whole-repo overview: every node laid out at once, coloured by
  cluster. A later view, distinct from the neighborhood canvas that is the Explorer's
  first surface.
