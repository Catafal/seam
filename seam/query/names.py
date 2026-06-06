"""Name-resolution helpers for the qualified<->bare edge bridging (Tier A, Slices 1+2).

LEAF MODULE — imports only stdlib + seam/config. Never imports engine, tools, or other
query sub-modules. Pattern mirrors seam/query/clusters.py.

ROOT CAUSE this module fixes:
  Seam stores method symbol names as the QUALIFIED string "Class.method" but stores
  call-edge target_name as the BARE identifier "method". This asymmetry means
  callers/callees of a qualified symbol never join in context(). This module provides:

  - bare_name(qualified) -> str
      The rightmost identifier after the last dot. If there is no dot the input is
      returned unchanged. Never raises on empty or malformed input.

  - edge_match_names(conn, name) -> list[str]
      The set of strings to use for edge table lookups (target_name IN / source_name IN).
      Returns [name] when name has no dot (bare — exact match only).
      Returns [name, bare_suffix] when name contains a dot, so a call edge stored as
      the bare form is also matched. Order is stable: qualified first, bare second.

  - resolve_query_to_defs(conn, name) -> list[sqlite3.Row]    [Slice 2]
      Resolve a query name to ALL matching symbol definition rows.
      Resolution order:
        1. Exact name match → return all rows with that exact name.
        2. If no exact match AND name has no dot (bare identifier):
           Scan for symbols whose name ends with ".{name}" (qualified defs).
           Filter in Python to ensure exact suffix match (not just LIKE match).
        3. If no exact match AND name has a dot → return [] (unknown qualified name).
      Never raises. Returns an empty list when nothing is found.

WHY not store bare name in the DB:
  Scope guard — Tier A is read-path-only. No schema change, no migration, no re-index.
  The bridging is pure read-time reconciliation using what is already stored.

WHY edge_match_names takes a conn param:
  Future slices (members expansion, Slice 3) will need DB queries to find all symbols
  whose qualified_name starts with "Class." The conn param is threaded through now so
  callers never need to change their call site when that capability is added.
  For Slice 1 the conn is accepted but not queried.
"""

import logging
import sqlite3

# seam/config imports are available but not needed by this slice's logic.
# The conn param is plumbed for future slice 3 DB-query expansion.

logger = logging.getLogger(__name__)


def bare_name(qualified: str) -> str:
    """Return the rightmost identifier after the last dot.

    Examples:
        "Class.method"     -> "method"
        "pkg.Class.method" -> "method"
        "authenticate"     -> "authenticate"  (no dot, returned unchanged)
        ""                 -> ""              (empty, returned unchanged)
        "Class."           -> ""              (trailing dot, bare part is empty)
        ".method"          -> "method"        (leading dot)

    Never raises. Handles all edge cases by relying on str.rsplit behaviour.
    """
    if "." not in qualified:
        return qualified
    # rsplit with maxsplit=1 gives ["prefix", "bare"] or ["", "bare"] for ".method"
    _, _, after = qualified.rpartition(".")
    return after


def edge_match_names(conn: sqlite3.Connection, name: str) -> list[str]:
    """Return the list of names to use for edges.target_name / edges.source_name lookups.

    The returned list is ordered and deduplicated:
      - When name has no dot:  [name]               (exact match only)
      - When name has a dot:   [name, bare_suffix]   (qualified first, then bare)

    WHY two names:
      Seam's extractor stores edge target_name as the bare identifier (e.g. "method")
      but symbol name as the qualified string ("Class.method"). Matching ONLY on the
      qualified name would miss all call edges; matching only on the bare would add
      false positives for other classes' methods with the same name. Including BOTH
      maximises recall while keeping the query simple (IN clause).

    WHY qualified first:
      The first entry is the "canonical" name the caller asked about. Keeping it first
      makes the list deterministic and lets future callers (e.g. debug logging) identify
      which match came from the exact vs. the bridged form.

    The conn parameter is accepted for API stability — future slices will extend this
    function with DB queries (member expansion for class-level context). In Slice 1
    no DB query is made; the function is pure.
    """
    # Defensive: always return a list[str] regardless of input
    if not name:
        return [name]

    bare = bare_name(name)

    # No dot in name — bare == name, return a single-element list (no dup)
    if bare == name:
        return [name]

    # Qualified name: return [qualified, bare], deduped
    # (bare != name since we checked above, so no duplicates possible here)
    return [name, bare]


def resolve_query_to_defs(conn: sqlite3.Connection, name: str) -> list[sqlite3.Row]:
    """Resolve a query name to ALL matching symbol definition rows (Slice 2).

    Resolution order:
      1. Exact name match — return all symbols where name == query.
         This preserves byte-stability for callers that already have exact names.
      2. Bare-name suffix scan (only when name has NO dot and exact returned nothing):
         Find all qualified symbols whose name ends with ".{name}" — e.g. querying
         "speakText" finds "TTS.speakText", "AudioPlayer.speakText". Filtered in Python
         to guarantee exact suffix match (LIKE alone would match "speakTextExtra").
      3. Qualified-but-absent: if name has a dot and exact returned nothing → return [].
         We never suffix-scan for partially-qualified queries (too ambiguous).

    Returns a list of sqlite3.Row objects ready for use in context() (fields: name, file,
    start_line, end_line, kind, docstring, signature, decorators, is_exported, visibility,
    qualified_name). Never raises. Returns [] when nothing is found.

    WHY separate from edge_match_names:
      edge_match_names returns strings for edge JOIN lookups (fast, no DB query).
      resolve_query_to_defs returns full symbol rows for definition aggregation — it
      IS a DB query and drives which defs context() merges. They serve different callers.

    WHY SQL LIKE + Python filter for the suffix scan:
      SQLite LIKE with '%.name' can match "speakTextEx" if there were a symbol "X.speakTextEx"
      where the LIKE matches "speakTextEx" → false positive. The Python filter checks that
      the suffix after the last dot is EXACTLY `name`, making the scan exact.
    """
    if not name:
        return []

    # Step 1: exact name match — always try this first.
    # Fetches all Phase 4 enrichment columns so context() can build a full ContextResult.
    exact_rows = conn.execute(
        """
        SELECT s.name, f.path AS file, s.start_line, s.end_line, s.kind, s.docstring,
               s.signature, s.decorators, s.is_exported, s.visibility, s.qualified_name
        FROM symbols s
        JOIN files f ON s.file_id = f.id
        WHERE s.name = ?
        ORDER BY s.id
        """,
        (name,),
    ).fetchall()

    if exact_rows:
        # Exact match found — return as-is, preserving byte-stability.
        return exact_rows

    # Step 2: bare-name suffix scan — only if name has no dot (bare identifier).
    # A query like "Class.method" that isn't in the index is treated as unknown.
    if "." in name:
        # Qualified name not found → unknown. No suffix scan for partially-qualified queries.
        return []

    # Candidate scan: find symbols whose name contains a dot and ends with ".{name}".
    # LIKE '%.name' is a pre-filter; Python confirms exact suffix to avoid false positives.
    suffix_pattern = f"%.{name}"
    candidate_rows = conn.execute(
        """
        SELECT s.name, f.path AS file, s.start_line, s.end_line, s.kind, s.docstring,
               s.signature, s.decorators, s.is_exported, s.visibility, s.qualified_name
        FROM symbols s
        JOIN files f ON s.file_id = f.id
        WHERE s.name LIKE ?
        ORDER BY s.id
        """,
        (suffix_pattern,),
    ).fetchall()

    # Python-side exact suffix filter: the bare part after the LAST dot must be exactly `name`.
    # This eliminates LIKE false-positives (e.g. "Foo.speakTextWrapper" for query "speakText").
    matched = [row for row in candidate_rows if bare_name(row["name"]) == name]

    if matched:
        logger.debug(
            "resolve_query_to_defs: bare '%s' resolved to %d qualified def(s): %s",
            name,
            len(matched),
            [r["name"] for r in matched],
        )

    return matched
