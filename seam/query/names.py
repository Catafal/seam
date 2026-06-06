"""Name-resolution helpers for the qualified<->bare edge bridging (Tier A, Slices 1-3).

LEAF MODULE — imports only stdlib + seam/config. Never imports engine, tools, or other
query sub-modules. Pattern mirrors seam/query/clusters.py.

ROOT CAUSE this module fixes:
  Seam stores method symbol names as the QUALIFIED string "Class.method" but stores
  call-edge target_name as the BARE identifier "method". This asymmetry means
  callers/callees of a qualified symbol never join in context(). This module provides:

  - bare_name(qualified) -> str
      The rightmost identifier after the last dot. If there is no dot the input is
      returned unchanged. Never raises on empty or malformed input.

  - is_container_symbol(conn, name) -> bool    [Slice 3]
      Returns True if the named symbol is a class/interface/struct (container).
      Returns False for functions, methods, unknown names, or empty string.
      Never raises.

  - get_member_names(conn, name) -> list[str]    [Slice 3]
      Return the bare member names of a class container (symbols WHERE name LIKE 'Class.%').
      Bounded by SEAM_NAME_EXPANSION_CAP. Returns [] for unknown/non-container names
      and for classes with zero indexed members (graceful, not an error). Never raises.

  - edge_match_names(conn, name) -> list[str]
      The set of strings to use for edge table lookups (target_name IN / source_name IN).
      Returns [name] when name has no dot (bare — exact match only).
      Returns [name, bare_suffix] when name contains a dot, so a call edge stored as
      the bare form is also matched. Order is stable: qualified first, bare second.
      [Slice 3] When name is a bare class/container, also appends member bare names
      (e.g. 'parse', 'validate') so callers of any member are union-matched.

  - resolve_query_to_defs(conn, name) -> list[sqlite3.Row]    [Slice 2]
      Resolve a query name to ALL matching symbol definition rows.
      Resolution order:
        1. Exact name match → return all rows with that exact name.
        2. If no exact match AND name has no dot (bare identifier):
           Scan for symbols whose name ends with ".{name}" (qualified defs).
           Filter in Python to ensure exact suffix match (not just LIKE match).
        3. If no exact match AND name has a dot → return [] (unknown qualified name).
      Never raises. Returns an empty list when nothing is found.

  - expand_impact_seeds(conn, name) -> list[str]    [Slice 4]
      Expand a query name into the set of walk() seed strings for impact/trace analysis.
      This bridges the qualified-symbol / bare-edge asymmetry at the walk() call boundary.

      Expansion rules (applied in order; always returns a deduped list):
        1. Qualified name (has dot): [name, bare_suffix]
           e.g. "Parser.parse" → ["Parser.parse", "parse"]
           Ensures walk() matches edges that store bare "parse" as target_name.
        2. Container name (class/struct/interface, no dot): [name] + bare member names
           e.g. "Parser" → ["Parser", "parse", "validate"]
           Ensures walk() matches callers of any method of the class.
        3. Bare non-container name: [name]
           e.g. "orchestrate" → ["orchestrate"]
           Exact match only — no expansion needed.

      The caller passes ALL returned seeds to walk() at once; walk() already handles
      multi-seed BFS (treats all seeds as the same starting level).
      Never raises. Returns [name] as a safe fallback on any error.

WHY not store bare name in the DB:
  Scope guard — Tier A is read-path-only. No schema change, no migration, no re-index.
  The bridging is pure read-time reconciliation using what is already stored.

WHY edge_match_names takes a conn param:
  Slice 3 uses DB queries to find all symbols whose name starts with "Class." so
  edge_match_names can include member bare names for container symbols.
"""

import logging
import sqlite3

from seam.config import SEAM_NAME_EXPANSION_CAP

logger = logging.getLogger(__name__)

# Symbol kinds that represent containers (have members in the graph).
# Closed vocabulary matching the schema comment: 'function' | 'class' | 'method' |
# 'interface' | 'type'. Rust/C/C++ use 'struct' (via graph_common kind mapping).
_CONTAINER_KINDS: frozenset[str] = frozenset({"class", "interface", "struct"})


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


def is_container_symbol(conn: sqlite3.Connection, name: str) -> bool:
    """Return True when the named symbol is a class/interface/struct (container kind).

    Containers are symbols whose members appear as "Class.member" qualified names in
    the index. This determines whether edge_match_names should fan out to member names.

    Resolution: queries the symbols table for ANY row with this exact name and checks
    if its kind is in _CONTAINER_KINDS. Only the first (lowest-id) row is checked —
    homonyms with conflicting kinds are pathological and rare in practice.

    Returns False for:
      - Unknown names (not in DB)
      - function / method / type kinds
      - empty string input
    Never raises.
    """
    if not name:
        return False
    try:
        row = conn.execute(
            "SELECT kind FROM symbols WHERE name = ? ORDER BY id LIMIT 1",
            (name,),
        ).fetchone()
        if row is None:
            return False
        return row["kind"] in _CONTAINER_KINDS
    except Exception:  # noqa: BLE001
        # Degrade gracefully — read path must never crash.
        logger.debug("is_container_symbol: DB error for name=%r", name, exc_info=True)
        return False


def get_member_names(conn: sqlite3.Connection, class_name: str) -> list[str]:
    """Return the bare names of all members of a class/container symbol.

    Queries for symbols whose name starts with "class_name." (the LIKE prefix) then
    filters in Python to ensure the prefix is exact (no false positives from LIKE).
    Returns bare names (the part after the last dot) bounded by SEAM_NAME_EXPANSION_CAP.

    Examples:
        class_name="Parser", members=["Parser.parse", "Parser.validate"]
        → returns ["parse", "validate"]

    Returns [] for:
      - Unknown/non-container names
      - Classes with zero indexed members (graceful — not an error)
      - Empty string input
    Never raises.
    """
    if not class_name:
        return []
    try:
        # LIKE 'Class.%' is the SQL pre-filter; Python confirms exact prefix below.
        # Cap at SEAM_NAME_EXPANSION_CAP + buffer to handle LIKE false-positives cleanly,
        # then trim to cap after the Python filter.
        candidate_rows = conn.execute(
            "SELECT name FROM symbols WHERE name LIKE ? ORDER BY id LIMIT ?",
            (f"{class_name}.%", SEAM_NAME_EXPANSION_CAP + 10),
        ).fetchall()
    except Exception:  # noqa: BLE001
        logger.debug("get_member_names: DB error for class=%r", class_name, exc_info=True)
        return []

    prefix = f"{class_name}."
    members: list[str] = []
    for row in candidate_rows:
        sym_name: str = row["name"]
        # Exact prefix check: must start with "Class." (LIKE may match "ClassExtra.method").
        if sym_name.startswith(prefix):
            member_bare = bare_name(sym_name)
            if member_bare and member_bare not in members:
                members.append(member_bare)
        if len(members) >= SEAM_NAME_EXPANSION_CAP:
            break

    if members:
        logger.debug(
            "get_member_names: class=%r -> %d member(s): %s",
            class_name,
            len(members),
            members,
        )
    return members


def edge_match_names(conn: sqlite3.Connection, name: str) -> list[str]:
    """Return the list of names to use for edges.target_name / edges.source_name lookups.

    The returned list is ordered and deduplicated:
      - When name has no dot AND is not a container:  [name]  (exact match only)
      - When name has no dot AND IS a container:      [name, member1, member2, ...]
          Container = class/interface/struct. Member bare names are included so that
          call edges to any member method are matched for the class context.
          Bounded by SEAM_NAME_EXPANSION_CAP (see seam/config.py).
      - When name has a dot:   [name, bare_suffix]   (qualified first, then bare)
          A qualified method name (Class.method) is NOT treated as a container —
          only the containing class would be, not the method itself.

    WHY two names for qualified:
      Seam's extractor stores edge target_name as the bare identifier (e.g. "method")
      but symbol name as the qualified string ("Class.method"). Matching ONLY on the
      qualified name would miss all call edges; matching only on the bare would add
      false positives for other classes' methods with the same name. Including BOTH
      maximises recall while keeping the query simple (IN clause).

    WHY member fan-out for containers (Slice 3):
      When name is a class, there are no call edges that target the class name itself
      (callers invoke methods, not the class). Expanding to member bare names unions
      all callers of "Class.method" edges into the class context result.

    WHY qualified first / container name first:
      The first entry is always the "canonical" name the caller asked about, making
      the list deterministic and allowing debug logging to identify which match came
      from the exact vs. the bridged form.
    """
    # Defensive: always return a list[str] regardless of input
    if not name:
        return [name]

    bare = bare_name(name)

    # Name contains a dot → it's a qualified method reference, not a container.
    # Return [qualified, bare] per Slice 1 logic. No member fan-out for methods.
    if bare != name:
        return [name, bare]

    # Bare name (no dot): check if it's a container to decide on member fan-out.
    member_names = get_member_names(conn, name) if is_container_symbol(conn, name) else []

    if not member_names:
        # Non-container bare name OR container with zero members → exact match only.
        return [name]

    # Container with members: [class_name] + member bare names (deduped, bounded by cap).
    # class_name itself first (canonical), then members in discovery order.
    result = [name]
    seen: set[str] = {name}
    for m in member_names:
        if m not in seen and len(result) <= SEAM_NAME_EXPANSION_CAP:
            result.append(m)
            seen.add(m)
    return result


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


def expand_impact_seeds(conn: sqlite3.Connection, name: str) -> list[str]:
    """Expand a query name into the set of walk() seed strings for impact/trace analysis.

    Bridges the qualified-symbol / bare-edge asymmetry: Seam stores symbol names as
    qualified strings ("Class.method") but stores call-edge target_name as the bare
    identifier ("method"). This makes walk() see no upstream for a qualified symbol.
    Seed expansion resolves this at the walk() call boundary — no schema change needed.

    Expansion rules (deduped, stable order):
      1. Qualified name (has dot): [name, bare_suffix]
         e.g. "Parser.parse" -> ["Parser.parse", "parse"]
         walk() with direction=upstream then finds edges targeting bare "parse".

      2. Container (class/struct/interface, no dot): [name] + member bare names
         e.g. "Parser" -> ["Parser", "parse", "validate"]
         Unions callers of all methods into a single walk() pass.
         Bounded by SEAM_NAME_EXPANSION_CAP (same cap as get_member_names).

      3. Non-container bare name: [name] (exact match only, no expansion)
         e.g. "orchestrate" -> ["orchestrate"]

    WHY this lives in names.py (leaf module):
      names.py is already the single source of truth for all qualified<->bare bridging.
      Placing expansion here keeps impact.py and flows.py thin — they just call this
      function and pass the results to walk(). Leaf layering: imports only stdlib + config.

    Never raises. Returns [name] on any error (safe fallback — degrades to pre-slice-4).
    """
    if not name:
        # Empty string — return as-is; walk() will handle gracefully.
        return [name]

    bare = bare_name(name)

    # Case 1: qualified name (contains a dot) → [qualified, bare].
    # A qualified method "Class.method" is NOT a container — skip member fan-out.
    if bare != name:
        # Deduplicate in case bare == name (not possible when bare != name, but defensive).
        result = [name]
        if bare and bare != name:
            result.append(bare)
        logger.debug(
            "expand_impact_seeds: qualified '%s' -> %s",
            name,
            result,
        )
        return result

    # Case 2: bare name — check if it is a container (class/struct/interface).
    member_names = get_member_names(conn, name) if is_container_symbol(conn, name) else []

    if member_names:
        # Container: [class_name] + member bare names (bounded by cap, deduped).
        result = [name]
        seen: set[str] = {name}
        for m in member_names:
            if m not in seen and len(result) <= SEAM_NAME_EXPANSION_CAP:
                result.append(m)
                seen.add(m)
        logger.debug(
            "expand_impact_seeds: container '%s' -> %d seed(s): %s",
            name,
            len(result),
            result,
        )
        return result

    # Case 3: bare non-container name — exact match only.
    logger.debug("expand_impact_seeds: bare non-container '%s' -> ['%s']", name, name)
    return [name]
