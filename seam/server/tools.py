"""MCP tool handlers — thin adapters between MCP protocol and query engine.

Each handler: validates input → clamps limits → calls query.engine or analysis →
relativizes file paths → returns MCP-compatible response dict.

No business logic here. Query logic lives in seam/query/engine.py.
Impact logic lives in seam/analysis/impact.py.

Error conventions (matching mcp-tools.yaml):
  {"error": "INVALID_INPUT", "message": "..."} — blank/whitespace input
  {"error": "INVALID_QUERY", "message": "..."} — bad FTS5 syntax (seam_search only)
"""

import logging
import sqlite3
from pathlib import Path
from typing import Any

import seam.config as config
from seam.analysis import flows as flows_module
from seam.analysis import impact as impact_module
from seam.analysis.affected import AffectedResult
from seam.analysis.affected import affected as run_affected
from seam.analysis.changes import (
    DEFAULT_BASE_REF,
    VALID_SCOPES,
    ChangeReport,
    NotAGitRepoError,
    detect_changes,
)
from seam.analysis.flows import EdgeHop, Hop
from seam.query import engine
from seam.query.clusters import cluster_members as query_cluster_members
from seam.query.clusters import list_clusters as query_list_clusters
from seam.query.comments import why as comments_why
from seam.query.pack import ContextPack, NeighborRef
from seam.query.pack import context_pack as run_context_pack

logger = logging.getLogger(__name__)

# ── Lean-output: heavy fields stripped when verbose=False ────────────────────

# These 6 fields are valuable in verbose mode but inflate every record unnecessarily
# when the agent only needs the core identity + signature.
# Keys are ABSENT (not null) in lean mode — lean mode's whole point is fewer bytes.
_HEAVY_FIELDS: frozenset[str] = frozenset({
    "decorators",
    "is_exported",
    "visibility",
    "qualified_name",
    "resolved_by",
    "best_candidate",
})


def _apply_verbosity(record: dict[str, Any], verbose: bool) -> dict[str, Any]:
    """Strip heavy enrichment fields from a record when verbose=False.

    Never mutates the input dict.

    verbose=True  → the SAME dict object is returned unchanged (zero-copy fast path;
                    callers build records inline and never mutate them post-call, so
                    returning the original is safe and byte-identical to pre-Phase-8).
    verbose=False → a NEW dict is returned without the 6 heavy keys (decorators,
                    is_exported, visibility, qualified_name, resolved_by,
                    best_candidate). signature and all core identity fields are kept.
    """
    if verbose:
        return record
    # Build a copy without the heavy fields; missing keys are silently skipped.
    return {k: v for k, v in record.items() if k not in _HEAVY_FIELDS}


# ── Limit bounds (from mcp-tools.yaml) ────────────────────────────────────────

_QUERY_LIMIT_MIN = 1
_QUERY_LIMIT_MAX = 50
_QUERY_LIMIT_DEFAULT = 10

_SEARCH_LIMIT_MIN = 1
_SEARCH_LIMIT_MAX = 100
_SEARCH_LIMIT_DEFAULT = 20

_IMPACT_DEPTH_DEFAULT = 3
_IMPACT_DIRECTION_DEFAULT = "upstream"

_TRACE_DEPTH_MIN = 1
_TRACE_DEPTH_MAX = 10
_TRACE_DEPTH_DEFAULT = 10


# ── Helpers ───────────────────────────────────────────────────────────────────


def _serialize_hop(hop: Hop, root: Path) -> dict[str, Any]:
    """Serialize a flows.Hop dict for JSON output with path relativization.

    Includes best_candidate (relativized) for AMBIGUOUS hops.
    """
    raw_candidate: str | None = hop.get("best_candidate")
    return {
        "from_name": hop["from_name"],
        "to_name": hop["to_name"],
        "kind": hop["kind"],
        "confidence": hop["confidence"],
        "resolved_by": hop.get("resolved_by"),
        "best_candidate": _relativize(raw_candidate, root) if raw_candidate is not None else None,
    }


def _serialize_edge_hop(hop: EdgeHop, root: Path | None = None) -> dict[str, Any]:
    """Serialize an EdgeHop TypedDict to a plain dict for JSON-safe output.

    Phase 5: includes resolved_by for provenance (null when not available).
    Includes best_candidate (relativized) for AMBIGUOUS hops.
    """
    raw_candidate = hop.get("best_candidate")
    return {
        "name": hop["name"],
        "kind": hop["kind"],
        "confidence": hop["confidence"],
        "resolved_by": hop.get("resolved_by"),  # Phase 5: null = unknown/fast-path
        # best_candidate for AMBIGUOUS hops; relativized when root is provided.
        "best_candidate": (
            _relativize(raw_candidate, root) if (raw_candidate is not None and root is not None)
            else raw_candidate
        ),
    }


def _relativize(abs_path: str, root: Path) -> str:
    """Return abs_path relative to root; falls back to abs_path if not under root."""
    try:
        return str(Path(abs_path).relative_to(root))
    except ValueError:
        return abs_path


def _clamp(value: int, lo: int, hi: int) -> int:
    """Clamp value to [lo, hi] inclusive."""
    return max(lo, min(hi, value))


def _invalid_input(message: str) -> dict[str, Any]:
    # Log so an operator can see what an agent actually sent (the error dict
    # otherwise vanishes into the agent's response with no server-side trace).
    logger.warning("rejected (INVALID_INPUT): %s", message)
    return {"error": "INVALID_INPUT", "message": message}


def _invalid_query(message: str) -> dict[str, Any]:
    logger.warning("rejected (INVALID_QUERY): %s", message)
    return {"error": "INVALID_QUERY", "message": message}


# ── Handlers ──────────────────────────────────────────────────────────────────


def handle_seam_query(
    conn: sqlite3.Connection,
    concept: str,
    root: Path,
    limit: int = _QUERY_LIMIT_DEFAULT,
) -> list[dict[str, Any]] | dict[str, Any]:
    """Handler for the seam_query MCP tool.

    Finds symbols related to a concept using hybrid FTS5 + 1-hop graph expansion.
    Returns a list of QueryResult dicts, or an error dict on bad input.

    Limit is clamped to [1, 50].

    NOTE: no `verbose` flag here. seam_query results carry NO Phase 4/5 enrichment
    fields (only symbol/file/line/score/callers_count/callees_count), so lean mode
    would be a no-op — query is enrichment-free, exactly like seam_search, and both
    are deliberately excluded from the verbose contract.
    """
    # Validate: concept must not be empty or whitespace-only
    if not concept or not concept.strip():
        return _invalid_input("concept must not be empty or whitespace-only")

    # Clamp limit to spec bounds
    safe_limit = _clamp(limit, _QUERY_LIMIT_MIN, _QUERY_LIMIT_MAX)

    # Mirror seam_search: a malformed FTS5 concept maps to INVALID_QUERY rather
    # than silently returning [] (which an agent would read as "no such code").
    try:
        results = engine.query(conn, concept.strip(), safe_limit)
    except sqlite3.OperationalError as exc:
        return _invalid_query(f"FTS5 query syntax error: {exc}")

    # Relativize file paths so consumers get portable paths.
    return [
        {
            "symbol": r["symbol"],
            "file": _relativize(r["file"], root),
            "line": r["line"],
            "score": r["score"],
            "callers_count": r["callers_count"],
            "callees_count": r["callees_count"],
        }
        for r in results
    ]


def handle_seam_context(
    conn: sqlite3.Connection,
    symbol: str,
    root: Path,
    verbose: bool = True,
) -> dict[str, Any] | None:
    """Handler for the seam_context MCP tool.

    Returns a ContextResult dict for a known symbol, None for unknown symbols,
    or an error dict on blank input.

    verbose=True (default): output byte-identical to pre-Phase-8.
    verbose=False: decorators, is_exported, visibility, qualified_name are omitted.
                   signature and all core fields are kept.
    """
    # Validate: symbol must not be empty or whitespace-only
    if not symbol or not symbol.strip():
        return _invalid_input("symbol must not be empty or whitespace-only")

    result = engine.context(conn, symbol.strip())

    if result is None:
        return None

    # Build the full record first, then apply verbosity stripping at the edge.
    # WHY build-then-strip: the record is always fully built in verbose mode (backward
    # compat); in lean mode _apply_verbosity removes only the 6 heavy keys.
    record = {
        "symbol": result["symbol"],
        "file": _relativize(result["file"], root),
        "line": result["line"],
        "end_line": result["end_line"],
        "kind": result["kind"],
        "docstring": result["docstring"],
        "callers": result["callers"],
        "callees": result["callees"],
        "ambiguous": result["ambiguous"],  # Phase 1: True when name collision detected
        "cluster_id": result["cluster_id"],  # Phase 2: None when not clustered
        "cluster_label": result["cluster_label"],  # Phase 2: None when not clustered
        "cluster_peers": result["cluster_peers"],  # Phase 2: [] when not clustered / solo
        # Phase 4: node enrichment fields (null when pre-v5 or extraction not available)
        "signature": result["signature"],
        "decorators": result["decorators"],
        "is_exported": result["is_exported"],
        "visibility": result["visibility"],
        "qualified_name": result["qualified_name"],
    }
    return _apply_verbosity(record, verbose)


def handle_seam_search(
    conn: sqlite3.Connection,
    text: str,
    root: Path,
    limit: int = _SEARCH_LIMIT_DEFAULT,
) -> list[dict[str, Any]] | dict[str, Any]:
    """Handler for the seam_search MCP tool.

    Full-text search across symbol names and docstrings (FTS5 BM25).
    Returns a list of SearchResult dicts, or an error dict on bad input.

    Limit is clamped to [1, 100].
    Maps sqlite3.OperationalError (FTS5 syntax error) to INVALID_QUERY.
    """
    # Validate: text must not be empty or whitespace-only
    if not text or not text.strip():
        return _invalid_input("text must not be empty or whitespace-only")

    # Clamp limit to spec bounds
    safe_limit = _clamp(limit, _SEARCH_LIMIT_MIN, _SEARCH_LIMIT_MAX)

    try:
        results = engine.search(conn, text.strip(), safe_limit)
    except sqlite3.OperationalError as exc:
        # FTS5 rejects malformed query syntax with OperationalError
        return _invalid_query(f"FTS5 query syntax error: {exc}")

    # Relativize file paths
    return [
        {
            "symbol": r["symbol"],
            "file": _relativize(r["file"], root),
            "line": r["line"],
            "snippet": r["snippet"],
            "score": r["score"],
        }
        for r in results
    ]


def _serialize_tier_entry(entry: dict[str, Any], root: Path, verbose: bool) -> dict[str, Any]:
    """Serialize a single TieredEntry dict from the analysis layer.

    Relativizes file paths, includes Phase 5 provenance fields, and applies
    verbosity stripping. Extracted to keep the main handler readable.
    """
    return _apply_verbosity({
        "name": entry["name"],
        "distance": entry["distance"],
        "confidence": entry["confidence"],
        # Phase 5: resolved_by carries import-promotion provenance.
        # null when name-count fast-path was used (repo_root absent or "off").
        "resolved_by": entry.get("resolved_by"),
        "tier": entry["tier"],
        "file": _relativize(entry["file"], root) if entry["file"] is not None else None,
        "is_test": entry["is_test"],
        # Phase 5: best_candidate surfaces the most-proximate declaring
        # file for AMBIGUOUS entries (PRD story 6). Null for non-AMBIGUOUS or
        # when proximity data was unavailable. Relativized like other file paths.
        "best_candidate": (
            _relativize(entry["best_candidate"], root)
            if entry.get("best_candidate") is not None
            else None
        ),
    }, verbose)


def handle_seam_impact(
    conn: sqlite3.Connection,
    target: str,
    root: Path,
    direction: str = _IMPACT_DIRECTION_DEFAULT,
    max_depth: int = _IMPACT_DEPTH_DEFAULT,
    include_tests: bool = True,
    verbose: bool = True,
    limit: int = config.SEAM_IMPACT_MAX_RESULTS,
) -> dict[str, Any]:
    """Handler for the seam_impact MCP tool.

    Computes blast radius for a target symbol: which symbols are affected if the
    target changes, grouped into risk tiers by distance.

    Args:
        conn:          Open SQLite connection.
        target:        Symbol name to analyze (must not be blank/whitespace).
        root:          Project root for path relativization. Each TieredEntry includes a
                       `file` field (absolute path from the analysis layer) which is
                       relativized to root before returning.
        direction:     "upstream" | "downstream" | "both". Default: "upstream".
        max_depth:     Max hops. Clamped to [1, 10]. Default: 3.
        include_tests: When True (default), test-file dependents are included and tagged
                       with is_test=True. When False, test-file entries are filtered out
                       from all tiers (production-only blast radius).
        verbose:       When True (default), output includes all Phase 4/5 enrichment fields.
                       When False, heavy fields (resolved_by, best_candidate, etc.) are
                       stripped from each entry — lean mode.
        limit:         Per-tier entry cap. Default: SEAM_IMPACT_MAX_RESULTS (25).
                       Entries arrive distance-ordered from the analysis layer (tiers group
                       by distance), so the kept slice is always the closest/highest-risk.
                       limit <= 0 means unlimited (all entries returned).

    Returns:
        A JSON-able dict with the impact result, or an error dict on bad input.
        Top-level keys always include `found`, `target`, and `risk_summary`.
        risk_summary is {direction: {tier: count}} computed from the FULL pre-cap
        result — it is always trustworthy even when entry lists are capped.
        NOTE: "full" means before the `limit` cap, but AFTER the include_tests filter —
        when include_tests=False, risk_summary counts the production-only blast radius
        (test dependents are already excluded), matching the entries actually returned.
        When any tier was capped, `truncated` is included: {direction: {tier: omitted}}.

        Shape for direction="upstream":
            {"found": bool, "target": str, "risk_summary": {...},
             "upstream": {"WILL_BREAK": [...], "LIKELY_AFFECTED": [...], "MAY_NEED_TESTING": [...]}}
        Shape for direction="both":
            {"found": bool, "target": str, "risk_summary": {...},
             "upstream": {...tiers...}, "downstream": {...tiers...}}

        Each entry in a tier list includes:
            file    (str | None) — relative path from project root; None for unindexed.
            is_test (bool)       — True if the entry's file is a test file.

    Error shapes:
        {"error": "INVALID_INPUT", "message": "..."} — blank target or invalid direction.
    """
    # Validate: target must not be empty or whitespace-only.
    if not target or not target.strip():
        return _invalid_input("target must not be empty or whitespace-only")

    # Validate direction before passing to impact (impact raises ValueError on bad direction,
    # but we want the standard INVALID_INPUT shape here in the handler).
    valid_directions = {"upstream", "downstream", "both"}
    if direction not in valid_directions:
        return _invalid_input(
            f"direction must be one of: {sorted(valid_directions)}; got {direction!r}"
        )

    # Clamp max_depth via impact module's own clamp helper (single source of truth).
    safe_depth = impact_module.clamp_depth(max_depth)

    raw = impact_module.impact(
        conn,
        target=target.strip(),
        direction=direction,
        max_depth=safe_depth,
        include_tests=include_tests,
        # Thread repo_root for Phase 5 import-promotion (root is already the project root).
        repo_root=root,
    )

    # Build the response: pass found/target through, relativize file paths in entries.
    response: dict[str, Any] = {
        "found": raw["found"],
        "target": raw["target"],
    }

    # Determine whether capping is active (limit <= 0 means unlimited).
    effective_limit = limit if limit > 0 else None

    # Build risk_summary and capped tiers for each direction key present in raw.
    # WHY compute summary first: risk_summary must reflect the FULL pre-cap result
    # (story 15) — we count before slicing so truncation cannot hide the true total.
    risk_summary: dict[str, dict[str, int]] = {}
    truncated: dict[str, dict[str, int]] = {}

    for dir_key in ("upstream", "downstream"):
        if dir_key not in raw:
            continue
        tier_group = raw[dir_key]

        # ── 1. Count BEFORE capping (risk_summary denominator) ────────────────
        dir_summary = {tier: len(entries) for tier, entries in tier_group.items()}
        risk_summary[dir_key] = dir_summary

        # ── 2. Apply per-tier cap + serialize ─────────────────────────────────
        capped_tiers: dict[str, list[dict[str, Any]]] = {}
        dir_truncated: dict[str, int] = {}

        for tier, entries in tier_group.items():
            # Slice keeps the closest/highest-risk entries WITHOUT a sort here. WHY it's
            # safe: WILL_BREAK (d=1) and LIKELY_AFFECTED (d=2) each contain a single
            # distance. MAY_NEED_TESTING spans d=3..max_depth, but the analysis layer's
            # walk() emits entries in BFS (ascending-distance) order, so entries[:N] still
            # keeps the closest. (Do NOT remove walk()'s ordering assuming tiers are
            # single-distance buckets — that holds only for the first two tiers.)
            if effective_limit is not None and len(entries) > effective_limit:
                kept = entries[:effective_limit]
                dir_truncated[tier] = len(entries) - effective_limit
            else:
                kept = entries
                dir_truncated[tier] = 0

            # Serialize each kept entry: relativize paths + apply verbose stripping.
            capped_tiers[tier] = [
                _serialize_tier_entry(entry, root, verbose)
                for entry in kept
            ]

        response[dir_key] = capped_tiers

        # Only include truncated for directions where something was actually dropped.
        if any(count > 0 for count in dir_truncated.values()):
            truncated[dir_key] = dir_truncated

    # risk_summary is always present — it is the honest summary of the full blast radius.
    response["risk_summary"] = risk_summary

    # truncated is only present when at least one tier was capped in any direction.
    # Absence signals "nothing was dropped" (omitted vs all-zero to reduce token cost).
    if truncated:
        response["truncated"] = truncated

    # Surface hidden_tests when present (include_tests=False filtered test dependents).
    # Lets MCP callers distinguish "no dependents" from "all dependents were tests and
    # were hidden" — without it, --production-only could read as a false-safe.
    if "hidden_tests" in raw:
        response["hidden_tests"] = raw["hidden_tests"]

    return response


def handle_seam_trace(
    conn: sqlite3.Connection,
    source: str,
    target: str,
    root: Path,
    max_depth: int = _TRACE_DEPTH_DEFAULT,
    verbose: bool = True,
) -> dict[str, Any]:
    """Handler for the seam_trace MCP tool.

    Finds the shortest call/dependency path from source to target, and also
    returns one-hop callers and callees for both symbols so the agent can see
    the immediate neighborhood alongside the path.

    Args:
        conn:       Open SQLite connection.
        source:     Starting symbol name (must not be blank/whitespace).
        target:     Destination symbol name (must not be blank/whitespace).
        root:       Project root for path relativization (not currently used —
                    paths in flows are symbol names, not file paths; kept for
                    API consistency with other handlers).
        max_depth:  Max hops for path finding. Clamped to [1, 10]. Default: 10.

    Returns:
        A JSON-able dict with:
            found       (bool)      — True if a path was found.
            source      (str)       — the queried source name (echoed back).
            target      (str)       — the queried target name (echoed back).
            paths       (list)      — list of paths; each path is a list of Hop dicts.
                                      Empty list when source and target are not connected.
            callers_source (list)   — one-hop callers of source (EdgeHop dicts).
            callees_source (list)   — one-hop callees of source (EdgeHop dicts).
            callers_target (list)   — one-hop callers of target (EdgeHop dicts).
            callees_target (list)   — one-hop callees of target (EdgeHop dicts).

        Each Hop dict:
            from_name   (str) — source of this edge
            to_name     (str) — target of this edge
            kind        (str) — 'call' | 'import'
            confidence  (str) — EXTRACTED | INFERRED | AMBIGUOUS

        Each EdgeHop dict:
            name        (str) — neighboring symbol
            kind        (str) — 'call' | 'import'
            confidence  (str) — EXTRACTED | INFERRED | AMBIGUOUS

    Error shapes:
        {"error": "INVALID_INPUT", "message": "..."} — blank source or target.
    """
    # Validate: source and target must not be empty or whitespace-only.
    if not source or not source.strip():
        return _invalid_input("source must not be empty or whitespace-only")
    if not target or not target.strip():
        return _invalid_input("target must not be empty or whitespace-only")

    # Clamp depth to valid range.
    safe_depth = _clamp(max_depth, _TRACE_DEPTH_MIN, _TRACE_DEPTH_MAX)

    clean_source = source.strip()
    clean_target = target.strip()

    # Find the shortest path from source to target.
    # Thread root as repo_root for Phase 5 import-promotion (root is the project root).
    paths = flows_module.trace(conn, clean_source, clean_target,
                               max_depth=safe_depth, repo_root=root)

    # Gather one-hop neighborhood for both symbols — useful context for the agent.
    # Thread repo_root so callers/callees also surface import-promotion provenance.
    callers_source = flows_module.callers(conn, clean_source, repo_root=root)
    callees_source = flows_module.callees(conn, clean_source, repo_root=root)
    callers_target = flows_module.callers(conn, clean_target, repo_root=root)
    callees_target = flows_module.callees(conn, clean_target, repo_root=root)

    # Serialize paths: each Path is list[Hop]; Hop is already a plain TypedDict.
    # Convert to plain dicts for JSON-safety.
    # Phase 5: include resolved_by for provenance (null when fast-path / not available).
    # Include best_candidate (relativized) for AMBIGUOUS hops.
    # Apply _apply_verbosity to each hop so lean mode strips resolved_by/best_candidate.
    serialized_paths = [
        [
            _apply_verbosity(_serialize_hop(hop, root), verbose)
            for hop in path
        ]
        for path in paths
    ]

    return {
        "found": len(paths) > 0,
        "source": clean_source,
        "target": clean_target,
        "paths": serialized_paths,
        "callers_source": [_apply_verbosity(_serialize_edge_hop(h, root), verbose) for h in callers_source],
        "callees_source": [_apply_verbosity(_serialize_edge_hop(h, root), verbose) for h in callees_source],
        "callers_target": [_apply_verbosity(_serialize_edge_hop(h, root), verbose) for h in callers_target],
        "callees_target": [_apply_verbosity(_serialize_edge_hop(h, root), verbose) for h in callees_target],
    }


# Default scope for seam_changes.
_CHANGES_SCOPE_DEFAULT = "working"
# Import the canonical default from analysis.changes to keep handler and analysis
# layer in sync — avoids silent drift when the default changes.
_CHANGES_BASE_REF_DEFAULT = DEFAULT_BASE_REF


def handle_seam_changes(
    conn: sqlite3.Connection,
    root: Path,
    base_ref: str = _CHANGES_BASE_REF_DEFAULT,
    scope: str = _CHANGES_SCOPE_DEFAULT,
) -> dict[str, Any]:
    """Handler for the seam_changes MCP tool.

    Diffs the working tree / staged set / branch against a git ref, maps each
    changed line range back to the symbols it touched, runs those through impact
    analysis, and returns an overall risk level plus the affected symbols.

    Args:
        conn:     Open SQLite connection (read-only).
        root:     Project root for path relativization AND the git repo root.
        base_ref: Git ref for scope="branch" comparisons (e.g. "main").
        scope:    One of "working", "staged", "branch". Default: "working".

    Returns:
        A JSON-able dict with the ChangeReport fields, paths relativized to root.
        On bad input: {"error": "INVALID_INPUT", "message": "..."}
        On non-git dir: {"error": "NOT_A_GIT_REPO", "message": "..."}

    Error shapes:
        INVALID_INPUT  — scope is not one of working/staged/branch.
        NOT_A_GIT_REPO — root is not a git repository or git is unavailable.
    """
    # Validate scope.
    if scope not in VALID_SCOPES:
        return _invalid_input(f"scope must be one of {sorted(VALID_SCOPES)}; got {scope!r}")

    # Validate base_ref is not blank (only used for branch scope, but validate always).
    if not base_ref or not base_ref.strip():
        return _invalid_input("base_ref must not be empty or whitespace-only")

    try:
        report: ChangeReport = detect_changes(
            conn,
            base_ref=base_ref.strip(),
            scope=scope,
            repo_root=root,
        )
    except NotAGitRepoError as exc:
        logger.warning("seam_changes: not a git repo: %s", exc)
        return {"error": "NOT_A_GIT_REPO", "message": str(exc)}

    # Relativize all file paths in the report to root.
    def _rel(p: str | None) -> str | None:
        if p is None:
            return None
        return _relativize(p, root)

    changed_symbols_out = [
        {
            "name": s["name"],
            "file": _rel(s["file"]),
            "kind": s["kind"],
            "start_line": s["start_line"],
            "end_line": s["end_line"],
            "changed_lines": s["changed_lines"],
        }
        for s in report["changed_symbols"]
    ]

    affected_out = [
        {
            "name": a["name"],
            "file": _rel(a["file"]),
            "tier": a["tier"],
            "confidence": a["confidence"],
            "distance": a["distance"],
        }
        for a in report["affected"]
    ]

    return {
        "changed_symbols": changed_symbols_out,
        "new_files": [_rel(f) for f in report["new_files"]],
        "affected": affected_out,
        "risk_level": report["risk_level"],
        "ambiguous_warning": report["ambiguous_warning"],
        "scope": report["scope"],
        "base_ref": report["base_ref"],
        # partial=True when changed symbols exceeded the cap (see ChangeReport docstring).
        "partial": report["partial"],
    }


def handle_seam_why(
    conn: sqlite3.Connection,
    root: Path,
    file: str | None = None,
    line: int | None = None,
    symbol: str | None = None,
) -> list[dict[str, Any]] | dict[str, Any]:
    """Handler for the seam_why MCP tool.

    Returns semantic comments (WHY/HACK/NOTE/TODO/FIXME) near a file location
    or a symbol. At least one of file or symbol is required.

    Args:
        conn:   Open SQLite connection.
        root:   Project root — used to resolve relative file paths to absolute,
                and to relativize output file paths.
        file:   File path (relative to root or absolute). When provided, the
                handler resolves it against root before passing to why().
        line:   Line number (1-based). Only meaningful with file.
        symbol: Symbol name to look up.

    Returns:
        List of comment dicts (file relativized to root) — empty list is valid.
        Error dict {"error": "INVALID_INPUT", ...} when neither file nor symbol given.
    """
    # Validate: at least one of file/symbol is required
    if file is None and symbol is None:
        return _invalid_input("at least one of 'file' or 'symbol' is required")

    # Resolve file path against root so why() gets an absolute path matching
    # the DB's stored absolute paths (indexed at absolute-path time).
    abs_file: str | None = None
    if file is not None:
        abs_file = str((root / file).resolve()) if not Path(file).is_absolute() else file

    hits = comments_why(conn, file=abs_file, line=line, symbol=symbol)

    # Relativize file paths for MCP consumers (consistent with other tools)
    return [
        {
            "file": _relativize(hit["file"], root),
            "line": hit["line"],
            "marker": hit["marker"],
            "text": hit["text"],
        }
        for hit in hits
    ]


def handle_seam_clusters(
    conn: sqlite3.Connection,
    root: Path,
    cluster_id: int | None = None,
) -> list[dict[str, Any]]:
    """Handler for the seam_clusters MCP tool.

    With no cluster_id:  returns [{id, label, size}] for all clusters.
    With a cluster_id:   returns [{name, file, line, kind}] for that cluster's members.
    File paths in member rows are relativized to root.

    Args:
        conn:       Open SQLite connection.
        root:       Project root for path relativization.
        cluster_id: Optional. When provided, returns member symbols of that cluster.

    Returns:
        List of cluster summary dicts (no id) or member dicts (with relativized file).
        Empty list when no clusters exist or the cluster ID is unknown.
    """
    if cluster_id is None:
        # List all clusters — no file paths to relativize
        clusters = query_list_clusters(conn)
        return [{"id": c["id"], "label": c["label"], "size": c["size"]} for c in clusters]

    # List members of a specific cluster — relativize file paths
    members = query_cluster_members(conn, cluster_id)
    return [
        {
            "name": m["name"],
            "file": _relativize(m["file"], root),
            "line": m["line"],
            "kind": m["kind"],
        }
        for m in members
    ]


def handle_seam_affected(
    conn: sqlite3.Connection,
    changed_files: list[str],
    root: Path,
    depth: int = config.SEAM_AFFECTED_DEPTH,
) -> dict[str, Any]:
    """Handler for the seam_affected MCP tool.

    Given a list of changed file paths, finds all test files that depend on
    symbols defined in those files (via upstream impact traversal).

    Args:
        conn:          Open SQLite connection (read-only).
        changed_files: List of file paths (absolute or relative to root).
                       Must not be empty.
        root:          Project root for path relativization and relative-path resolution.
        depth:         Max traversal depth for upstream impact. Default from config.

    Returns:
        A dict with keys:
            changed_files          — relativized paths of input files
            affected_tests         — relativized paths of affected test files (sorted)
            total_dependents_traversed — count of all dependent entries traversed
        Or an error dict on invalid input:
            {"error": "INVALID_INPUT", "message": "..."}
    """
    # Validate: empty input is not useful and likely an agent mistake.
    if not changed_files:
        return _invalid_input("changed_files must not be empty")

    # Clamp: reject oversized file lists (SEAM_MAX_AFFECTED_FILES cap).
    # An agent accidentally passing the entire repo diff should get a clear error,
    # not a silent O(n * symbols) traversal. Mirrors the _clamp discipline of other handlers.
    max_files = config.SEAM_MAX_AFFECTED_FILES
    if len(changed_files) > max_files:
        return _invalid_input(
            f"changed_files length {len(changed_files)} exceeds maximum {max_files}; "
            "split the file list into smaller batches"
        )

    # Run the core affected-tests algorithm.
    result: AffectedResult = run_affected(
        conn,
        changed_files,
        depth=depth,
        repo_root=root,
    )

    # Relativize all file paths so the MCP consumer gets portable paths.
    # The analysis layer returns absolute paths (DB storage contract);
    # the handler contract (like all other handlers) is to relativize before returning.
    return {
        "changed_files": [_relativize(p, root) for p in result["changed_files"]],
        "affected_tests": [_relativize(p, root) for p in result["affected_tests"]],
        "total_dependents_traversed": result["total_dependents_traversed"],
        # partial=True when a file exceeded SEAM_MAX_AFFECTED_SYMBOLS; result may be incomplete.
        "partial": result["partial"],
    }


def handle_seam_context_pack(
    conn: sqlite3.Connection,
    symbol: str,
    root: Path,
    verbose: bool = True,
) -> dict[str, Any] | None:
    """Handler for the seam_context_pack MCP tool.

    Returns a fully-enriched context bundle for a symbol, or None for unknown
    symbols, or an error dict on blank input.

    The bundle contains:
        target        — full 360-degree ContextResult (file paths relativized)
        callers       — enriched 1-hop callers (NeighborRef, capped, paths relativized)
        callees       — enriched 1-hop callees (NeighborRef, capped, paths relativized)
        why           — WHY/HACK/NOTE/TODO/FIXME comments (capped)
        cluster_peers — functional-area peers from target
        truncated     — {callers, callees, comments} counts of dropped entries

    Mirrors handle_seam_context's contract:
        - blank/whitespace → INVALID_INPUT error dict
        - unknown symbol   → None
        - found symbol     → serialized ContextPack with paths relativized

    verbose=True (default): output byte-identical to pre-Phase-8.
    verbose=False: heavy fields stripped from target and each neighbor.
    """
    # Validate: symbol must not be empty or whitespace-only
    if not symbol or not symbol.strip():
        return _invalid_input("symbol must not be empty or whitespace-only")

    pack: ContextPack | None = run_context_pack(
        conn,
        symbol.strip(),
    )

    if pack is None:
        return None

    # Relativize file path in target (mirrors handle_seam_context).
    # Apply _apply_verbosity so lean mode strips heavy fields from the target record.
    target = pack["target"]
    serialized_target = _apply_verbosity({
        "symbol": target["symbol"],
        "file": _relativize(target["file"], root),
        "line": target["line"],
        "end_line": target["end_line"],
        "kind": target["kind"],
        "docstring": target["docstring"],
        "callers": target["callers"],
        "callees": target["callees"],
        "ambiguous": target["ambiguous"],
        "cluster_id": target["cluster_id"],
        "cluster_label": target["cluster_label"],
        "cluster_peers": target["cluster_peers"],
        "signature": target["signature"],
        "decorators": target["decorators"],
        "is_exported": target["is_exported"],
        "visibility": target["visibility"],
        "qualified_name": target["qualified_name"],
    }, verbose)

    # Relativize file paths in enriched neighbors.
    # WHY direct key access (not .get()): NeighborRef is a TypedDict with all
    # required keys — using .get() would silently return None for a renamed field
    # instead of raising a KeyError that makes the bug visible.
    # Apply _apply_verbosity so lean mode strips heavy fields from each neighbor.
    def _serialize_neighbor(nb: NeighborRef) -> dict[str, Any]:
        return _apply_verbosity({
            "name": nb["name"],
            "file": _relativize(nb["file"], root),
            "line": nb["line"],
            "kind": nb["kind"],
            "signature": nb["signature"],
            "decorators": nb["decorators"],
            "is_exported": nb["is_exported"],
            "visibility": nb["visibility"],
            "qualified_name": nb["qualified_name"],
        }, verbose)

    return {
        "target": serialized_target,
        "callers": [_serialize_neighbor(nb) for nb in pack["callers"]],
        "callees": [_serialize_neighbor(nb) for nb in pack["callees"]],
        "why": [
            {
                "file": _relativize(hit["file"], root),
                "line": hit["line"],
                "marker": hit["marker"],
                "text": hit["text"],
            }
            for hit in pack["why"]
        ],
        "cluster_peers": pack["cluster_peers"],
        "truncated": pack["truncated"],
    }
