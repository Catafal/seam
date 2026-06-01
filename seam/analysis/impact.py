"""Impact analysis — blast-radius assessment for a target symbol.

Given a symbol name and direction, runs the traversal walk and buckets
reachable symbols into risk tiers by distance.

Public interface:
    ``impact(conn, target, direction, max_depth) -> ImpactResult``

Risk tiers (from PRD / CLAUDE.md):
    WILL_BREAK       — distance 1 (direct dependents — definitely affected)
    LIKELY_AFFECTED  — distance 2 (indirect dependents — probably affected)
    MAY_NEED_TESTING — distance 3+ (transitive dependents — test to be sure)

ImpactResult structure:
    A dict with the following top-level keys:
        found   — bool: True if the target exists as a symbol or edge endpoint;
                  False if not found in the index (unknown symbol / typo).
        target  — str: the queried symbol name.
        <direction-key(s)> — TierGroup dicts.

    For direction in {"upstream", "downstream"}:
        {"found": bool, "target": str, <direction>: TierGroup}
    For direction="both":
        {"found": bool, "target": str, "upstream": TierGroup, "downstream": TierGroup}

    TierGroup is a dict mapping tier name -> list of TieredEntry.

TieredEntry structure:
    name        — symbol name
    distance    — hop count from the target
    confidence  — path confidence (EXTRACTED | INFERRED | AMBIGUOUS)
    tier        — risk tier string (WILL_BREAK | LIKELY_AFFECTED | MAY_NEED_TESTING)
    file        — absolute file path if the name is an indexed symbol; None otherwise

Unknown symbol handling:
    If the target has no edges and no matching symbol, return an ImpactResult
    with found=False and empty TierGroups (NOT an error, NOT None).
    This lets callers distinguish "symbol not found" from "found but no dependents".

Import hierarchy:
    analysis.impact imports from analysis.traversal only.
    No imports from server, cli, or query.
"""

import logging
import sqlite3
from typing import Any

from seam.analysis.traversal import _SQL_VAR_BATCH, Reached, walk

logger = logging.getLogger(__name__)

# ── Tier name constants ────────────────────────────────────────────────────────
# Must match exactly the tier names in CLAUDE.md d=1/2/3.

TIER_WILL_BREAK = "WILL_BREAK"           # distance == 1
TIER_LIKELY_AFFECTED = "LIKELY_AFFECTED" # distance == 2
TIER_MAY_NEED_TESTING = "MAY_NEED_TESTING"  # distance >= 3

# Depth bounds for clamping user input.
_MIN_DEPTH = 1
_MAX_DEPTH = 10
_DEFAULT_DEPTH = 3

# Valid direction values.
_VALID_DIRECTIONS = {"upstream", "downstream", "both"}

# ── Public types ───────────────────────────────────────────────────────────────

# TieredEntry is a plain dict rather than a TypedDict so that the `file: str | None`
# field can coexist with the other `str`/`int` fields without mypy complaints about
# mixed-type TypedDicts. The shape is documented below and in CONTRACT.md.
#
# TieredEntry shape:
#   name        (str)        — symbol name
#   distance    (int)        — hops from the target
#   confidence  (str)        — EXTRACTED | INFERRED | AMBIGUOUS
#   tier        (str)        — WILL_BREAK | LIKELY_AFFECTED | MAY_NEED_TESTING
#   file        (str | None) — absolute path if name is an indexed symbol; else None

# TierGroup maps tier-name -> list of entries for that tier.
# e.g. {"WILL_BREAK": [...], "LIKELY_AFFECTED": [...], "MAY_NEED_TESTING": [...]}
TierGroup = dict[str, list[dict[str, Any]]]

# ImpactResult: a plain dict with keys: found, target, and direction-key(s).
# Using Any for the value type because the dict holds bool/str/TierGroup values.
ImpactResult = dict[str, Any]

# ── Internal helpers ───────────────────────────────────────────────────────────


def _distance_to_tier(distance: int) -> str:
    """Map a hop distance to a risk tier name.

    d=1  -> WILL_BREAK
    d=2  -> LIKELY_AFFECTED
    d=3+ -> MAY_NEED_TESTING
    """
    if distance == 1:
        return TIER_WILL_BREAK
    if distance == 2:
        return TIER_LIKELY_AFFECTED
    return TIER_MAY_NEED_TESTING


def _lookup_files_for_names(
    conn: sqlite3.Connection,
    names: list[str],
) -> dict[str, str]:
    """Return a mapping of symbol name -> absolute file path for indexed symbols.

    Names that are not indexed symbols (external import targets, unresolved names, etc.)
    are absent from the returned dict (callers should treat absence as file=None).

    When a name maps to multiple symbols (ambiguous, same name in multiple files),
    the first match is chosen deterministically by ORDER BY files.path.

    Uses _SQL_VAR_BATCH chunking to avoid SQLite's SQLITE_MAX_VARIABLE_NUMBER limit.
    """
    if not names:
        return {}

    file_map: dict[str, str] = {}

    for batch_start in range(0, len(names), _SQL_VAR_BATCH):
        batch = names[batch_start : batch_start + _SQL_VAR_BATCH]
        placeholders = ",".join("?" * len(batch))

        # Join symbols to files to get the absolute path. ORDER BY path for determinism
        # when multiple symbols share the same name across files.
        sql = f"""
            SELECT s.name, f.path
            FROM symbols s
            JOIN files f ON f.id = s.file_id
            WHERE s.name IN ({placeholders})
            ORDER BY f.path
        """
        rows = conn.execute(sql, batch).fetchall()

        for row in rows:
            # Only record the first (deterministic) result for each name.
            if row["name"] not in file_map:
                file_map[row["name"]] = row["path"]

    return file_map


def _symbol_exists(conn: sqlite3.Connection, target: str) -> bool:
    """Return True if target appears as a symbol name or an edge source/target.

    This covers:
      - A fully indexed symbol (in symbols table).
      - A symbol that only appears as a source/target in edges (external caller, etc.)
        but was never extracted as a definition.
    """
    # Check symbols table first (cheapest common case).
    row = conn.execute("SELECT 1 FROM symbols WHERE name = ? LIMIT 1", (target,)).fetchone()
    if row is not None:
        return True

    # Check edges table: target might be an external symbol not indexed as a definition.
    row = conn.execute(
        "SELECT 1 FROM edges WHERE source_name = ? OR target_name = ? LIMIT 1",
        (target, target),
    ).fetchone()
    return row is not None


def _build_tier_group(
    reached: list[Reached],
    file_map: dict[str, str],
) -> TierGroup:
    """Convert a list of Reached symbols into a TierGroup dict.

    Initializes all three tier keys so callers can always index without KeyError,
    even if some tiers are empty. Each entry includes a `file` field (absolute
    path for indexed symbols; None for names not in the symbols table).
    """
    group: TierGroup = {
        TIER_WILL_BREAK: [],
        TIER_LIKELY_AFFECTED: [],
        TIER_MAY_NEED_TESTING: [],
    }
    for r in reached:
        tier = _distance_to_tier(r["distance"])
        group[tier].append(
            {
                "name": r["name"],
                "distance": r["distance"],
                "confidence": r["confidence"],
                "tier": tier,
                "file": file_map.get(r["name"]),  # None if name is not an indexed symbol
            }
        )
    return group


# ── Public interface ───────────────────────────────────────────────────────────


def clamp_depth(depth: int) -> int:
    """Clamp max_depth to the valid range [1, 10]. Exported for use by handlers."""
    return max(_MIN_DEPTH, min(_MAX_DEPTH, depth))


def impact(
    conn: sqlite3.Connection,
    target: str,
    direction: str = "upstream",
    max_depth: int = _DEFAULT_DEPTH,
) -> ImpactResult:
    """Compute the blast radius of a symbol.

    Args:
        conn:       Open SQLite connection (read-only; no writes).
        target:     Symbol name to analyze. Unknown symbols return found=False.
        direction:  "upstream" (who depends on target), "downstream" (what target
                    depends on), or "both" (full neighborhood). Default: "upstream".
        max_depth:  Maximum hops from target. Clamped to [1, 10]. Default: 3.

    Returns:
        ImpactResult dict with the following keys:
            found   (bool) — True if target is a known symbol or edge endpoint.
            target  (str)  — the queried name (echoed back for agent convenience).
            <direction-key(s)>:
                "upstream"   -> {"upstream": TierGroup}
                "downstream" -> {"downstream": TierGroup}
                "both"       -> {"upstream": TierGroup, "downstream": TierGroup}

            All TierGroup dicts always contain all three tier keys
            (WILL_BREAK, LIKELY_AFFECTED, MAY_NEED_TESTING), even if empty.

            Each TieredEntry includes:
                name        (str)        — symbol name
                distance    (int)        — hops from target
                confidence  (str)        — EXTRACTED | INFERRED | AMBIGUOUS
                tier        (str)        — risk tier name
                file        (str | None) — absolute path; None if not an indexed symbol

        found=False: target not in the index. Empty TierGroups are included.
        found=True, empty tiers: target is indexed but has no dependents in the given direction.

    Raises:
        ValueError: if direction is not one of the three valid values.
    """
    if direction not in _VALID_DIRECTIONS:
        raise ValueError(
            f"Invalid direction {direction!r}. Must be one of: {sorted(_VALID_DIRECTIONS)}"
        )

    safe_depth = clamp_depth(max_depth)

    # Determine existence before walking — lets callers distinguish "not found" from
    # "found but isolated". This is a cheap indexed lookup.
    found = _symbol_exists(conn, target)

    # Collect all reached symbols so we can batch the file lookup.
    upstream_reached: list[Reached] = []
    downstream_reached: list[Reached] = []

    if direction in ("upstream", "both"):
        upstream_reached = walk(conn, [target], "upstream", safe_depth)

    if direction in ("downstream", "both"):
        downstream_reached = walk(conn, [target], "downstream", safe_depth)

    # Batch lookup of files for all reached symbol names (single query per batch).
    all_names = [r["name"] for r in upstream_reached] + [r["name"] for r in downstream_reached]
    file_map = _lookup_files_for_names(conn, all_names)

    # Build result structure.
    result: ImpactResult = {"found": found, "target": target}

    if direction in ("upstream", "both"):
        result["upstream"] = _build_tier_group(upstream_reached, file_map)

    if direction in ("downstream", "both"):
        result["downstream"] = _build_tier_group(downstream_reached, file_map)

    logger.debug(
        "impact(target=%r, direction=%r, max_depth=%d, found=%s) -> %s",
        target,
        direction,
        safe_depth,
        found,
        {k: sum(len(v) for v in tg.values()) for k, tg in result.items() if isinstance(tg, dict)},
    )

    return result
