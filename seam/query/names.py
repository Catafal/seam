"""Name-resolution helpers for the qualified<->bare edge bridging (Tier A, Slice 1).

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

WHY not store bare name in the DB:
  Scope guard — Tier A is read-path-only. No schema change, no migration, no re-index.
  The bridging is pure read-time reconciliation using what is already stored.

WHY edge_match_names takes a conn param:
  Future slices (members expansion, Slice 3) will need DB queries to find all symbols
  whose qualified_name starts with "Class." The conn param is threaded through now so
  callers never need to change their call site when that capability is added.
  For Slice 1 the conn is accepted but not queried.
"""

import sqlite3

# seam/config imports are available but not needed by this slice's logic.
# The conn param is plumbed for future slice 3 DB-query expansion.


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
