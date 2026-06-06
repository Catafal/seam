"""Impact analysis — blast-radius assessment for a target symbol.

Given a symbol name and direction, runs the traversal walk and buckets
reachable symbols into risk tiers by distance.

Public interface:
    ``impact(conn, target, direction, max_depth, repo_root=None) -> ImpactResult``

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
    resolved_by — Phase 5: how confidence was decided (from walk); None for pre-wired rows
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
from pathlib import Path
from typing import Any

from seam.analysis.testpaths import is_test_file
from seam.analysis.traversal import _SQL_VAR_BATCH, Reached, walk
from seam.query.names import expand_impact_seeds

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


def _test_only_file_stems(conn: sqlite3.Connection) -> set[str]:
    """Return file STEMS that belong exclusively to test files in the index.

    WHY this exists: import edges are sourced at the importing file's STEM (e.g.
    `test_fts` for tests/unit/test_fts.py), not at an indexed symbol. So a test file
    that does `from seam.query.fts import rescore` shows up as an upstream dependent
    named `test_fts` with file=None — which is_test_file(None) tags False, leaking
    test dependents into the "production-only" blast radius (include_tests=False).

    Production code that calls `fts.rescore()` imports the MODULE (`fts`), so the only
    import edges that target a bare symbol like `rescore` come from test files — exactly
    the entries we must hide. Mapping such a stem back to its test file lets is_test
    tagging catch them.

    Conservative by construction: a stem is returned ONLY when EVERY file with that
    stem is a test file. If a production file shares the stem, the stem is omitted so a
    real production dependent is never hidden (we'd rather show a stray test than hide
    production code). Single query; result is small (one entry per file).
    """
    test_stems: set[str] = set()
    prod_stems: set[str] = set()
    try:
        for row in conn.execute("SELECT path FROM files").fetchall():
            stem = Path(row["path"]).stem
            (test_stems if is_test_file(row["path"]) else prod_stems).add(stem)
    except sqlite3.Error:
        # Degrade gracefully: no stem refinement, behave as before (never raises).
        return set()
    return test_stems - prod_stems


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
    test_stems: set[str] | None = None,
) -> TierGroup:
    """Convert a list of Reached symbols into a TierGroup dict.

    Initializes all three tier keys so callers can always index without KeyError,
    even if some tiers are empty. Each entry includes:
      - file        (str | None) — absolute path for indexed symbols; None otherwise
      - is_test     (bool)       — True when the entry's declaring file is a test file
                                   (per is_test_file()), OR — for a file=None import-edge
                                   source — when its name is a test-only file stem (see
                                   _test_only_file_stems). False for production files and
                                   for unresolved names that are not test-file stems.
      - resolved_by (str | None) — Phase 5 provenance string from walk(); None for
                                   fast-path (name-count only) or pre-Phase-5 rows.

    The `file` field contract is unchanged — only is_test tagging is refined for
    file=None import-source entries so the production-only blast radius is genuinely
    test-free. test_stems=None means "no refinement" (the pre-existing behavior).
    """
    stems = test_stems or set()
    group: TierGroup = {
        TIER_WILL_BREAK: [],
        TIER_LIKELY_AFFECTED: [],
        TIER_MAY_NEED_TESTING: [],
    }
    for r in reached:
        tier = _distance_to_tier(r["distance"])
        file_path = file_map.get(r["name"])  # None if name is not an indexed symbol
        group[tier].append(
            {
                "name": r["name"],
                "distance": r["distance"],
                "confidence": r["confidence"],
                # Phase 5: resolved_by from walk() (None for fast-path / no import context)
                "resolved_by": r.get("resolved_by"),
                "tier": tier,
                "file": file_path,
                # is_test_file(None) returns False — safe default for unresolved names.
                # Refinement: a file=None import-edge source whose name is a test-only
                # file stem (e.g. `test_fts`) is a test dependent → tag it is_test so the
                # production-only filter hides it. Does NOT touch the `file` field.
                "is_test": is_test_file(file_path) or (file_path is None and r["name"] in stems),
                # Phase 5: best_candidate is the highest-proximity declaring file for
                # AMBIGUOUS entries (PRD story 6). None for non-AMBIGUOUS or unavailable.
                "best_candidate": r.get("best_candidate"),
            }
        )
    return group


# ── Public interface ───────────────────────────────────────────────────────────


def clamp_depth(depth: int) -> int:
    """Clamp max_depth to the valid range [1, 10]. Exported for use by handlers."""
    return max(_MIN_DEPTH, min(_MAX_DEPTH, depth))


def _filter_tests_from_tier_group(tier_group: TierGroup) -> TierGroup:
    """Return a new TierGroup with all is_test=True entries removed.

    All three tier keys are preserved (even if their lists become empty after
    filtering) so callers can always index without KeyError.
    """
    return {
        tier: [entry for entry in entries if not entry.get("is_test", False)]
        for tier, entries in tier_group.items()
    }


def _count_test_entries(tier_group: TierGroup) -> int:
    """Count is_test=True entries across all tiers (how many include_tests=False hides)."""
    return sum(
        1
        for entries in tier_group.values()
        for entry in entries
        if entry.get("is_test", False)
    )


def impact(
    conn: sqlite3.Connection,
    target: str,
    direction: str = "upstream",
    max_depth: int = _DEFAULT_DEPTH,
    include_tests: bool = True,
    repo_root: Path | None = None,
) -> ImpactResult:
    """Compute the blast radius of a symbol.

    Args:
        conn:          Open SQLite connection (read-only; no writes).
        target:        Symbol name to analyze. Unknown symbols return found=False.
        direction:     "upstream" (who depends on target), "downstream" (what target
                       depends on), or "both" (full neighborhood). Default: "upstream".
        max_depth:     Maximum hops from target. Clamped to [1, 10]. Default: 3.
        include_tests: When True (default), all dependents are returned — test files
                       included, tagged with is_test=True. When False, entries whose
                       file lives in a test tree are filtered out from all tiers.
                       Entries with file=None (unresolved names) are always included
                       because their provenance is unknown (is_test=False by rule).
        repo_root:     Repository root for Phase 5 import-promotion resolution.
                       When provided and SEAM_IMPORT_RESOLUTION="on", hop confidence
                       is resolved via resolve_edge() so imported homonyms promote
                       to EXTRACTED 'import'.  None → name-count resolution only.

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
                is_test     (bool)       — True if file is a test file; False otherwise

            When include_tests=False, the result also carries:
                hidden_tests (int) — number of test-file dependents that were
                                     filtered out. Lets callers tell "no dependents"
                                     (hidden_tests==0) from "only test dependents,
                                     all hidden" (hidden_tests>0) — the latter is
                                     NOT safe to treat as dead code.

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

    # Expand the target name to a set of walk() seeds — bridges the qualified-symbol /
    # bare-edge asymmetry. e.g. "Parser.parse" -> ["Parser.parse", "parse"] so that
    # walk() matches edges whose target_name is the bare "parse" (as stored by the extractor).
    # For a class seed like "Parser" -> ["Parser", "parse", "validate"] to aggregate
    # all member callers. The seeds list is deduped and bounded by config cap.
    seeds = expand_impact_seeds(conn, target)
    logger.debug("impact: target=%r expanded to seeds=%s", target, seeds)

    # Collect all reached symbols so we can batch the file lookup.
    upstream_reached: list[Reached] = []
    downstream_reached: list[Reached] = []

    if direction in ("upstream", "both"):
        upstream_reached = walk(conn, seeds, "upstream", safe_depth, repo_root=repo_root)

    if direction in ("downstream", "both"):
        downstream_reached = walk(conn, seeds, "downstream", safe_depth, repo_root=repo_root)

    # Batch lookup of files for all reached symbol names (single query per batch).
    all_names = [r["name"] for r in upstream_reached] + [r["name"] for r in downstream_reached]
    file_map = _lookup_files_for_names(conn, all_names)

    # Build result structure: tag each entry with is_test, then optionally filter.
    # When include_tests=False we also count what we removed and expose it as
    # `hidden_tests`, so callers can distinguish "no dependents at all" from
    # "all dependents were tests and got filtered" — the latter is NOT safe to
    # treat as dead code (tests would break). Without this signal include_tests=False
    # could give a dangerous false-safe.
    result: ImpactResult = {"found": found, "target": target}
    hidden_tests = 0

    # Compute test-only file stems ONLY when filtering (include_tests=False) so the
    # is_test tag catches file=None import-edge sources from test files. Skipped when
    # include_tests=True so seam_changes (which calls impact() with the analysis-layer
    # default) pays zero extra cost and keeps byte-stable risk verdicts.
    test_stems = _test_only_file_stems(conn) if not include_tests else None

    if direction in ("upstream", "both"):
        tier_group = _build_tier_group(upstream_reached, file_map, test_stems)
        if not include_tests:
            hidden_tests += _count_test_entries(tier_group)
            tier_group = _filter_tests_from_tier_group(tier_group)
        result["upstream"] = tier_group

    if direction in ("downstream", "both"):
        tier_group = _build_tier_group(downstream_reached, file_map, test_stems)
        if not include_tests:
            hidden_tests += _count_test_entries(tier_group)
            tier_group = _filter_tests_from_tier_group(tier_group)
        result["downstream"] = tier_group

    # Only include hidden_tests in the result when test filtering was actually applied.
    # Its absence signals "tests included in the output" to callers — no ambiguity.
    # Its presence tells callers how many dependents were hidden, which is the
    # anti-false-safe signal: hidden_tests>0 means "there ARE test dependents, they
    # were just filtered", so an agent cannot conclude the symbol is unused or safe
    # to delete because it sees empty tiers. hidden_tests==0 means filtering produced
    # the same result as not filtering (no test dependents existed to remove).
    if not include_tests:
        result["hidden_tests"] = hidden_tests

    logger.debug(
        "impact(target=%r, direction=%r, max_depth=%d, include_tests=%s, found=%s) -> %s",
        target,
        direction,
        safe_depth,
        include_tests,
        found,
        {k: sum(len(v) for v in tg.values()) for k, tg in result.items() if isinstance(tg, dict)},
    )

    return result
