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
from seam.analysis.flows import EdgeHop
from seam.query import engine
from seam.query.clusters import cluster_members as query_cluster_members
from seam.query.clusters import list_clusters as query_list_clusters
from seam.query.comments import why as comments_why

logger = logging.getLogger(__name__)

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


def _serialize_edge_hop(hop: EdgeHop) -> dict[str, Any]:
    """Serialize an EdgeHop TypedDict to a plain dict for JSON-safe output."""
    return {
        "name": hop["name"],
        "kind": hop["kind"],
        "confidence": hop["confidence"],
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

    # Relativize file paths so consumers get portable paths
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
) -> dict[str, Any] | None:
    """Handler for the seam_context MCP tool.

    Returns a ContextResult dict for a known symbol, None for unknown symbols,
    or an error dict on blank input.
    """
    # Validate: symbol must not be empty or whitespace-only
    if not symbol or not symbol.strip():
        return _invalid_input("symbol must not be empty or whitespace-only")

    result = engine.context(conn, symbol.strip())

    if result is None:
        return None

    return {
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


def handle_seam_impact(
    conn: sqlite3.Connection,
    target: str,
    root: Path,
    direction: str = _IMPACT_DIRECTION_DEFAULT,
    max_depth: int = _IMPACT_DEPTH_DEFAULT,
    include_tests: bool = True,
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

    Returns:
        A JSON-able dict with the impact result, or an error dict on bad input.
        Top-level keys always include `found` (bool) and `target` (str).
        Shape for direction="upstream":
            {"found": bool, "target": str,
             "upstream": {"WILL_BREAK": [...], "LIKELY_AFFECTED": [...], "MAY_NEED_TESTING": [...]}}
        Shape for direction="both":
            {"found": bool, "target": str,
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
    )

    # Build the response: pass found/target through, relativize file paths in entries.
    response: dict[str, Any] = {
        "found": raw["found"],
        "target": raw["target"],
    }

    # Relativize each TieredEntry's `file` field using the provided root.
    # `file` is an absolute path (or None) from the analysis layer.
    # Pass is_test through so MCP callers can see the tag.
    for dir_key in ("upstream", "downstream"):
        if dir_key not in raw:
            continue
        tier_group = raw[dir_key]
        response[dir_key] = {
            tier: [
                {
                    "name": entry["name"],
                    "distance": entry["distance"],
                    "confidence": entry["confidence"],
                    "tier": entry["tier"],
                    "file": _relativize(entry["file"], root) if entry["file"] is not None else None,
                    "is_test": entry["is_test"],
                }
                for entry in entries
            ]
            for tier, entries in tier_group.items()
        }

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
    paths = flows_module.trace(conn, clean_source, clean_target, max_depth=safe_depth)

    # Gather one-hop neighborhood for both symbols — useful context for the agent.
    callers_source = flows_module.callers(conn, clean_source)
    callees_source = flows_module.callees(conn, clean_source)
    callers_target = flows_module.callers(conn, clean_target)
    callees_target = flows_module.callees(conn, clean_target)

    # Serialize paths: each Path is list[Hop]; Hop is already a plain TypedDict.
    # Convert to plain dicts for JSON-safety.
    serialized_paths = [
        [
            {
                "from_name": hop["from_name"],
                "to_name": hop["to_name"],
                "kind": hop["kind"],
                "confidence": hop["confidence"],
            }
            for hop in path
        ]
        for path in paths
    ]

    return {
        "found": len(paths) > 0,
        "source": clean_source,
        "target": clean_target,
        "paths": serialized_paths,
        "callers_source": [_serialize_edge_hop(h) for h in callers_source],
        "callees_source": [_serialize_edge_hop(h) for h in callees_source],
        "callers_target": [_serialize_edge_hop(h) for h in callers_target],
        "callees_target": [_serialize_edge_hop(h) for h in callees_target],
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
