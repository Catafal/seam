# Lessons — Seam

> Record AI mistakes, gotchas, and non-obvious discoveries here.
> Format: date, what went wrong, why, fix.
> After 5+ entries in one category, promote patterns to CLAUDE.md.

---

## 2026-06-01 — tree-sitter is error-recovering (affects parser contract)

- **What:** IMPLEMENTATION_PLAN 3.3 expected `parse_*` to return `None` for
  malformed source files. Tree-sitter does NOT fail on syntax errors — it returns
  a tree containing `ERROR` nodes.
- **Why it matters:** A test asserting "malformed → None" can never pass.
- **Fix:** `parse_*` returns `None` only for: unreadable file, binary file
  (null byte in first 1KB), or size > `SEAM_MAX_FILE_BYTES`. Malformed source
  still returns a (possibly partial) tree. Parsers must never raise.

## 2026-06-01 — JavaScript parsed via the TSX grammar (no separate JS dep)

- **What:** Tech stack ships only `tree-sitter-typescript`. There is no
  `tree-sitter-javascript` dependency.
- **Fix:** Parse `.js/.mjs/.cjs` with the **TSX** grammar (a superset that
  handles JS + JSX). Acceptable for Phase 0; revisit if JS-specific nodes are needed.

## 2026-06-01 — Edge field names differ from DB column names (by design)

- **What:** In-memory `Edge` uses `source`/`target`; the `edges` table columns
  are `source_name`/`target_name`.
- **Fix:** `db.upsert_file` owns the translation. Do not rename either side.
  See `docs/CONTRACT.md`.

## 2026-06-01 — PRAGMA foreign_keys is PER-CONNECTION (review finding, fixed)

- **What:** The schema sets `PRAGMA foreign_keys = ON`, but that only applies to
  the connection that ran it. The watcher (the only long-lived writer) opened a
  raw connection without it, so `INSERT OR REPLACE`/`delete_file` did NOT cascade
  → orphaned symbols/edges on every auto-save.
- **Fix:** All connections now go through `db.connect()`, which sets
  `foreign_keys = ON` + `busy_timeout = 5000` every time. `upsert_file` also uses
  `ON CONFLICT(path) DO UPDATE` (stable id) and explicitly deletes edges AND
  symbols by `file_id` (deleting symbols does NOT cascade to edges).

## 2026-06-01 — Accepted Phase-0 limitations (documented, NOT bugs to fix now)

These surfaced in review and are intentional Phase-0 scope per DISCOVERY.md /
the string-name-edge ADR. Revisit in Phase 1:
- **extract_edges is intentionally rough** (per user direction): calls inside
  arrow functions, `import * as ns`, `from x import *`, and aliased-import local
  bindings are not fully resolved. Import/simple-call edges are captured.
- **String-name edges cause name-collision counting**: two symbols sharing a name
  share `callers_count`/`callees_count`, and `context()` on a duplicated name
  returns one arbitrary definition. Inherent to the ADR (edges store names, not
  IDs, for independent re-indexing). Acceptable for Phase 0.
- **`seam status` freshness is a heuristic**: detects modified/added tracked
  files only; deletions and brand-new untracked files are not reflected (the live
  watcher handles those in real time).

## 2026-06-01 — String-name collision limitation MITIGATED via confidence tagging (Phase 1 issue #3)

The Phase-0 string-name collision limitation is now mitigated rather than silently wrong:
- **Edge confidence** (`EXTRACTED | INFERRED | AMBIGUOUS`) is assigned at extraction time
  and persisted in the DB. An AMBIGUOUS edge signals a known name collision; EXTRACTED
  signals high certainty; INFERRED signals a heuristic edge (outside same-file symbol set).
- **context() ambiguous flag**: `ContextResult.ambiguous` is set to `True` when multiple
  symbols share the requested name in the DB (cross-file collision). The first match is
  still returned, but the caller is informed to disambiguate.
- **Scope of resolution**: confidence at extraction time is resolved against the same-file
  symbol list only (pure function, no DB access). Cross-file ambiguity is detected at
  query time by `engine.context()`. This is a known and documented limitation — not a bug.
- **Schema migration**: v1 databases get `edges.confidence` added via `ALTER TABLE` in
  `init_db`; existing rows receive the DEFAULT value ('EXTRACTED'). A re-index is logged
  as recommended (old edges may not have accurate confidence). Never crashes.
