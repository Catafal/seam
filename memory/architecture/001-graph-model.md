# Architecture: Graph Data Model

**Pattern:** Adjacency list in SQLite (not a native graph DB)
**Why:** Zero dependencies; FTS5 built-in; single-file storage; adequate for 1-2 hop traversal

Nodes = `symbols` table (id, name, kind, file_id, start_line, end_line, docstring)
Edges = `edges` table (source_name, target_name, kind, file_id, line)

FTS5 virtual table `symbols_fts` mirrors `symbols.name + docstring` via triggers.

**Key invariant:** Edges use string names (not symbol IDs) so they survive independent re-indexing of either endpoint.
