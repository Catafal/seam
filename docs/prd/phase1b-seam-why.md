# PRD — Phase 1b: seam_why (semantic comment nodes)

> Slice of Phase 1b. New capability from DISCOVERY ("# WHY: parsing"). Status: ready-for-agent.

## Problem Statement

As an AI agent about to change a piece of code, I cannot cheaply recover the *intent* behind
it — the "why is this retry here?", "this is a HACK around X", "NOTE: keep in sync with Y"
rationale that lives in comments. `seam_query`/`seam_search` find code by name/docstring, and
`seam_context` shows callers/callees, but none surface the inline rationale comments that
explain *why* the code is shaped the way it is. So I either miss load-bearing constraints or
re-derive them by reading the whole file.

## Solution

As an agent, I want `seam_why` to return the **semantic comments** near a location or a symbol —
the marker-tagged rationale comments (`# WHY:`, `# HACK:`, `# NOTE:`, `# TODO:`, `# FIXME:`) —
so that before I edit `connect()` I can ask "why is this like this?" and immediately see the
documented intent and known caveats, instead of guessing.

- `seam why <file>` → all semantic comments in that file.
- `seam why <file>:<line>` → semantic comments near that line.
- `seam why --symbol <name>` → semantic comments inside or just above that symbol.

## User Stories

1. As an agent, I want semantic comments extracted during indexing, so that `seam_why` is instant (no re-parse at query time).
2. As an agent, I want only marker-tagged comments stored (`WHY`/`HACK`/`NOTE`/`TODO`/`FIXME`), so that the result is high-signal and plain comments don't drown the rationale.
3. As an agent, I want markers matched case-insensitively with an optional colon (`# WHY:`, `# why`, `// Hack:`), so that real-world comment styles are caught.
4. As an agent, I want Python `#` comments and TS/JS `//` and `/* */` comments supported, so that both language families are covered.
5. As an agent, I want each result to carry `marker`, `text`, `line`, and `file`, so that I know what kind of note it is, what it says, and where it lives.
6. As an agent, I want `seam why <file>` to return every semantic comment in the file, so that I can scan a file's caveats at once.
7. As an agent, I want `seam why <file>:<line>` to return comments within a small radius of that line, so that I can focus on the rationale around a specific spot.
8. As an agent, I want `seam why --symbol <name>` to return comments inside the symbol's body and just above its definition, so that I get the rationale attached to a function/class by name without knowing its line numbers.
9. As an agent, I want an empty list (not an error) when a file/symbol has no semantic comments, so that "no rationale recorded" is a clean, distinguishable answer.
10. As an agent, I want the MCP `seam_why` tool to return file paths relativized to the project root, consistent with the other tools.
11. As a maintainer, I want comments stored in their own table keyed to the file (cascade-deleted on re-index), so that re-indexing a file replaces its comments atomically like symbols and edges.
12. As a maintainer, I want a guarded schema migration to v3 that adds the `comments` table without destroying existing data, and that tells the user to re-index to populate it.
13. As a maintainer, I want the marker set and the proximity radii documented in one place, so that the behavior is predictable.
14. As an agent, I want comment extraction to never raise (return [] on any parse trouble), consistent with the "parsers never raise" rule, so that one odd file can't abort indexing.

## Implementation Decisions

- **New `comments` table (schema v3).** Columns: `id` (PK), `file_id` (FK → files ON DELETE CASCADE), `line` (int), `marker` (text, normalized UPPERCASE), `text` (text — the comment body after the marker). Index on `file_id`. No FTS (lookup is by file/line/symbol proximity, not full-text) — keeps it simple; FTS can be added later if needed.
- **Migration v2→v3 (guarded, additive).** `schema.sql` adds the `comments` table via `CREATE TABLE IF NOT EXISTS` (runs on every `init_db`, so existing DBs gain the empty table automatically). A `_run_migration_v2_to_v3` guard bumps `schema_version` to `'3'` exactly once and logs an info message telling the user to run `seam init` to populate comments (existing indexes have no comments until re-indexed). Fail-loud on error, like the v1→v2 guard. No data loss.
- **Marker set (decision: fixed).** `WHY`, `HACK`, `NOTE`, `TODO`, `FIXME`. Matched case-insensitively, optional trailing colon, at the start of the comment body (after the `#`/`//`/`/*` delimiter and whitespace). Stored marker is normalized to uppercase. Documented as a module constant.
- **Extraction — `extract_comments(node, language, filepath) -> list[Comment]` in `graph.py`.** Walks tree-sitter `comment` nodes; for each, strips the delimiter, checks the body against the marker regex, and emits a `Comment` TypedDict (`marker`, `text`, `line`) when matched. Python: `#` line comments. TS/JS: `//` line comments and `/* ... */` blocks (match the marker on the first non-empty body line). Pure, never raises (returns [] on trouble), mirroring `extract_symbols`/`extract_edges`.
- **Pipeline wiring.** `index_one_file` calls `extract_comments` and passes the list to `upsert_file`, which gains a `comments` parameter and (within its existing single transaction) deletes the file's old comments and inserts the new ones — exactly the symbols/edges pattern.
- **Read path (decision: both file-location and symbol).** A new query-layer function `why(conn, *, file=None, line=None, symbol=None) -> list[CommentHit]`:
  - `file` given, no `line` → all comments for that resolved file path.
  - `file` + `line` → comments within ±RADIUS lines of `line` (documented constant).
  - `symbol` given → resolve the symbol's file + `[start_line - LEAD, end_line]` range (LEAD documented) and return comments in that range. (Symbol resolves via the symbols table; ambiguous name → use the first by file path, deterministic, like impact's file lookup.)
  - Lives in a new small module (e.g. `seam/query/comments.py`) to respect the 1000-line/file limit and keep `engine.py` focused. Read-only; analysis/query layer (no server/cli imports).
- **MCP + CLI surfaces.** `handle_seam_why` (relativizes `file`), `seam_why` MCP tool (`file: str|None`, `line: int|None`, `symbol: str|None`), and `seam why` CLI accepting `<file>` or `<file>:<line>` positional plus `--symbol`. Validation: at least one of file/symbol required → INVALID_INPUT otherwise.
- **Config from config.py.** No new env needed for the fixed marker set; the marker list is a documented constant in the extractor module (not env-driven, per the chosen "fixed set").

## Testing Decisions

- **What makes a good test:** assert external behavior — given a file with marked and unmarked comments, `extract_comments` returns exactly the marked ones with correct marker/text/line; given an indexed file, `why()` returns the right comments for file, file+line, and symbol lookups; non-matches and empty cases return [].
- **Modules tested:**
  - `extract_comments` — each marker (Python `#`, TS `//`, TS `/* */`), case-insensitivity, optional colon, plain comments ignored, marker-like-but-not (`# whyever`) not matched, never raises on odd input.
  - `why()` — file mode returns all; file+line mode respects the radius (in-range included, out-of-range excluded); symbol mode returns comments in/above the symbol and excludes others; unknown file/symbol → []; at-least-one-arg validation.
  - migration — opening a v2 DB bumps schema_version to '3' and the comments table exists; idempotent on a v3/fresh DB.
  - handler/CLI wiring — `seam_why` relativizes paths; `seam why file:line` parses the line.
- **Prior art:** `tests/unit/test_*` for graph extraction (`test_confidence.py`, `test_richer_edges.py`), `tests/integration/test_changes.py` / `test_impact_handler.py` for handler wiring, the migration test pattern from the v1→v2 work.
- **TDD:** write the failing `extract_comments` test first (marked vs unmarked), then the `why()` file/line/symbol tests.

## Out of Scope

- Full-text search over comments (no FTS table) — lookup is proximity-based.
- Configurable marker set via env — the chosen design is the fixed set; revisit later if needed.
- Multi-line rationale blocks spanning several comment lines merged into one entry — each matched comment line is its own entry (simplest; agents can read adjacent lines).
- Linking a comment to the *specific* symbol it documents beyond the proximity heuristic (no comment→symbol FK) — proximity (file/line/symbol-range) is the contract.
- Go/Rust comments — that arrives with the Go/Rust parser slice.
- Editing/round-tripping comments — `seam_why` is read-only.

## Further Notes

- `seam_why` was listed as `status: PHASE_2` in `mcp-tools.yaml`; this slice promotes it to implemented under Phase 1b per the project roadmap. Update its status + spec there.
- Honesty: only marker-tagged comments are stored, so `seam_why` never returns noise; an empty result genuinely means "no recorded rationale here."
- After merge, existing indexes must run `seam init` once to populate the new `comments` table (the migration creates it empty).
