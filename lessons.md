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
