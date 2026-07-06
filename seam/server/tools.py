"""MCP tool handlers — thin facade + remaining handlers.

This module is the public surface of the handler layer. It:
  1. Re-exports everything from handler_common, impact_handler, trace_handler so
     that all existing imports (`from seam.server.tools import X`) continue to work
     unchanged. seam/server/mcp.py, web.py, cli/main.py, cli/read.py, and all tests
     require zero edits.
  2. Implements the remaining 10 handlers: query, context, search, changes, why,
     clusters, flows, affected, context_pack, structure.

Split performed in Slice 2, P2 #103. Pure mechanical refactor — byte-identical output.

No business logic here. Query logic lives in seam/query/engine.py.
Impact logic lives in seam/analysis/impact.py.

Error conventions (matching mcp-tools.yaml):
  {"error": "INVALID_INPUT", "message": "..."} — blank/whitespace input
  {"error": "INVALID_QUERY", "message": "..."} — bad FTS5 syntax (seam_search only)
"""

import logging
import sqlite3
from pathlib import Path
from typing import Any, cast

import seam.config as config
from seam.analysis.affected import AffectedResult
from seam.analysis.affected import affected as run_affected
from seam.analysis.changes import (
    DEFAULT_BASE_REF,
    VALID_SCOPES,
    ChangeReport,
    NotAGitRepoError,
    detect_changes,
)
from seam.analysis.processes import Flow, build_flow, list_entry_points
from seam.query import engine
from seam.query.architecture import describe_architecture
from seam.query.clusters import cluster_members as query_cluster_members
from seam.query.clusters import list_clusters as query_list_clusters
from seam.query.comments import why as comments_why
from seam.query.graph_search import graph_search as run_graph_search
from seam.query.pack import ContextPack, NeighborRef
from seam.query.pack import context_pack as run_context_pack
from seam.query.pack_evidence import RelationshipEvidence
from seam.query.plan import EvidenceRef, InspectionItem, PlanResult, plan_diff, plan_target
from seam.query.schema import describe_schema
from seam.query.snippet import snippet as run_snippet
from seam.query.structure import StructureResult
from seam.query.structure import build_structure as run_build_structure
from seam.server.grounding_handler import handle_seam_grounding  # noqa: F401 — re-exported

# ── Re-exports from sibling modules (facade — public surface unchanged) ───────
# Everything imported by mcp.py / web.py / main.py / tests keeps working.
from seam.server.handler_common import (  # noqa: F401 — re-exported as public API
    _HEAVY_FIELDS,
    _IMPACT_DEPTH_DEFAULT,
    _IMPACT_DIRECTION_DEFAULT,
    _QUERY_LIMIT_DEFAULT,
    _QUERY_LIMIT_MAX,
    _QUERY_LIMIT_MIN,
    _SEARCH_LIMIT_DEFAULT,
    _SEARCH_LIMIT_MAX,
    _SEARCH_LIMIT_MIN,
    _TRACE_DEPTH_DEFAULT,
    _TRACE_DEPTH_MAX,
    _TRACE_DEPTH_MIN,
    _TRACE_ENDPOINT_CAND_CAP,
    _apply_verbosity,
    _clamp,
    _invalid_input,
    _invalid_query,
    _maybe_attach_staleness,
    _qualified_trace_candidates,
    _relativize,
    _resolve_uid,
    _serialize_edge_hop,
    _serialize_hop,
    _trace_not_found,
    compute_uid,
)
from seam.server.impact_handler import (  # noqa: F401 — re-exported as public API
    _BYTE_CEILING_TRUNCATED_RESERVE,
    _STEER_RESERVE_MARGIN,
    _apply_byte_ceiling,
    _attach_steer,
    _compute_self_context,
    _count_direction_entries,
    _prioritize_tier_entries,
    _serialize_tier_entry,
    _shape_tier_group,
    handle_seam_impact,
)
from seam.server.suspects_handler import handle_seam_suspects  # noqa: F401 — re-exported
from seam.server.trace_handler import handle_seam_trace  # noqa: F401 — re-exported

logger = logging.getLogger(__name__)

# ── Remaining handlers ────────────────────────────────────────────────────────


def handle_seam_query(
    conn: sqlite3.Connection,
    concept: str,
    root: Path,
    limit: int = _QUERY_LIMIT_DEFAULT,
    *,
    semantic: bool = True,
) -> list[dict[str, Any]] | dict[str, Any]:
    """Handler for the seam_query MCP tool.

    Finds symbols related to a concept using hybrid FTS5 + 1-hop graph expansion.
    Returns a list of QueryResult dicts, or an error dict on bad input.

    Limit is clamped to [1, 50].

    semantic=True (default): use hybrid path when available.
    semantic=False: force keyword-only FTS5 (bypasses hybrid without mutating config).

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
        results = engine.query(conn, concept.strip(), safe_limit, semantic=semantic)
    except sqlite3.OperationalError as exc:
        return _invalid_query(f"FTS5 query syntax error: {exc}")

    # Relativize file paths so consumers get portable paths.
    # uid is computed from the ABSOLUTE path (r["file"]) BEFORE relativizing, so it
    # round-trips through _resolve_uid (P6c). It lets a follow-up context/impact/trace
    # call pin this exact symbol by handle — no homonym re-disambiguation round-trip.
    return [
        {
            "symbol": r["symbol"],
            "uid": compute_uid(r["file"], r["line"]),
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
    *,
    uid: str | None = None,
) -> dict[str, Any] | None:
    """Handler for the seam_context MCP tool.

    Returns a ContextResult dict for a known symbol, None for unknown symbols,
    or an error dict on blank input.

    uid (P6c): an optional stable handle (from a search/query result) that pins the
    EXACT (file, line) symbol — an alternative to `symbol` that bypasses homonym
    ambiguity and saves the disambiguation round-trip. When provided, `symbol` is
    ignored. An unknown uid returns None (same not-found contract as an unknown name).

    verbose=True (default): output byte-identical to pre-Phase-8.
    verbose=False: decorators, is_exported, visibility, qualified_name are omitted.
                   signature and all core fields are kept.
    """
    if uid is not None:
        # UID path: resolve the handle to the exact declaring (file, line) and build
        # context for THAT row — not the first homonym by name.
        resolved = _resolve_uid(conn, uid)
        if resolved is None:
            return None
        _name, abs_file, line = resolved
        result = engine.context_at(conn, abs_file, line)
    else:
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
        # A3: field-access split — always [] for non-field/non-class seeds.
        "field_readers": result["field_readers"],
        "field_writers": result["field_writers"],
        # P3.3: static test evidence, separated from production callers/callees.
        "test_callers": result["test_callers"],
        "tested_symbols": result["tested_symbols"],
    }
    context_result = _apply_verbosity(record, verbose)
    # P2: attach staleness banner LAST — purely additive, byte-identical when fresh.
    return _maybe_attach_staleness(context_result, conn, root)


def handle_seam_search(
    conn: sqlite3.Connection,
    text: str,
    root: Path,
    limit: int = _SEARCH_LIMIT_DEFAULT,
    *,
    semantic: bool = True,
) -> list[dict[str, Any]] | dict[str, Any]:
    """Handler for the seam_search MCP tool.

    Full-text search across symbol names and docstrings (FTS5 BM25).
    Returns a list of SearchResult dicts, or an error dict on bad input.

    Limit is clamped to [1, 100].
    Maps sqlite3.OperationalError (FTS5 syntax error) to INVALID_QUERY.

    semantic=True (default): use hybrid path when available.
    semantic=False: force keyword-only FTS5 (bypasses hybrid without mutating config).
    """
    # Validate: text must not be empty or whitespace-only
    if not text or not text.strip():
        return _invalid_input("text must not be empty or whitespace-only")

    # Clamp limit to spec bounds
    safe_limit = _clamp(limit, _SEARCH_LIMIT_MIN, _SEARCH_LIMIT_MAX)

    try:
        results = engine.search(conn, text.strip(), safe_limit, semantic=semantic)
    except sqlite3.OperationalError as exc:
        # FTS5 rejects malformed query syntax with OperationalError
        return _invalid_query(f"FTS5 query syntax error: {exc}")

    # Relativize file paths. uid is computed from the ABSOLUTE path before
    # relativizing so it resolves back to this exact symbol (P6c).
    return [
        {
            "symbol": r["symbol"],
            "uid": compute_uid(r["file"], r["line"]),
            "file": _relativize(r["file"], root),
            "line": r["line"],
            "snippet": r["snippet"],
            "score": r["score"],
        }
        for r in results
    ]


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

    changes_result: dict[str, Any] = {
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
    # P2: attach staleness banner LAST — purely additive; risk_level etc. unchanged.
    return _maybe_attach_staleness(changes_result, conn, root)


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


def handle_seam_flows(
    conn: sqlite3.Connection,
    root: Path,
    entry: str | None = None,
) -> Flow | dict[str, Any] | None:
    """Handler for the seam_flows MCP tool — execution-flow discovery.

    With no entry: returns {"entry_points": [{name, kind, file, reach}]} — the
    program's top execution starting points (call-graph roots ranked by how much
    they reach downstream: CLI commands, web routes, MCP handlers, main, …).

    With an entry: returns that entry point's Flow tree (forward call-chain
    expansion, depth/breadth-capped), or None when the name is unknown — the MCP
    boundary normalizes None to {"found": false}.

    File paths are relativized to root. Confidence on each step uses the fast
    name-count resolver (a flow is an overview; use seam_impact/seam_trace for
    import-promoted confidence).

    Args:
        conn:  Open SQLite connection (read-only).
        root:  Project root for path relativization.
        entry: Optional entry-point symbol name. None → list mode.
    """
    if entry is None:
        return {"entry_points": list_entry_points(conn, repo_root=root)}
    entry = entry.strip()
    if not entry:
        return _invalid_input("entry must not be empty or whitespace-only")
    return build_flow(conn, entry, repo_root=root)


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
    affected_result: dict[str, Any] = {
        "changed_files": [_relativize(p, root) for p in result["changed_files"]],
        "affected_tests": [_relativize(p, root) for p in result["affected_tests"]],
        "total_dependents_traversed": result["total_dependents_traversed"],
        # partial=True when a file exceeded SEAM_MAX_AFFECTED_SYMBOLS; result may be incomplete.
        "partial": result["partial"],
    }
    # P2: attach staleness banner LAST — purely additive; risk verdicts unchanged.
    return _maybe_attach_staleness(affected_result, conn, root)


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
        relationship_evidence
                     — direct edge metadata supporting caller/callee claims
        caveats      — static-analysis and truncation limits agents must respect
        recommended_next_calls
                     — concrete follow-up Seam tool calls for verification

    Mirrors handle_seam_context's contract:
        - blank/whitespace → INVALID_INPUT error dict
        - unknown symbol   → None
        - found symbol     → serialized ContextPack with paths relativized

    verbose=True (default): keeps target/neighbor enrichment fields.
    verbose=False: strips heavy fields from target and each neighbor. Compact
    relationship evidence remains present so the pack's claims stay auditable.
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
    serialized_target = _apply_verbosity(
        {
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
        },
        verbose,
    )

    # Relativize file paths in enriched neighbors.
    # WHY direct key access (not .get()): NeighborRef is a TypedDict with all
    # required keys — using .get() would silently return None for a renamed field
    # instead of raising a KeyError that makes the bug visible.
    # Apply _apply_verbosity so lean mode strips heavy fields from each neighbor.
    def _serialize_neighbor(nb: NeighborRef) -> dict[str, Any]:
        return _apply_verbosity(
            {
                "name": nb["name"],
                "file": _relativize(nb["file"], root),
                "line": nb["line"],
                "kind": nb["kind"],
                "signature": nb["signature"],
                "decorators": nb["decorators"],
                "is_exported": nb["is_exported"],
                "visibility": nb["visibility"],
                "qualified_name": nb["qualified_name"],
            },
            verbose,
        )

    def _serialize_relationship_edge(edge: RelationshipEvidence) -> dict[str, Any]:
        return {
            "source": edge["source"],
            "target": edge["target"],
            "direction": edge["direction"],
            "kind": edge["kind"],
            "file": _relativize(edge["file"], root),
            "line": edge["line"],
            "confidence": edge["confidence"],
            "receiver": edge["receiver"],
            "synthesized_by": edge["synthesized_by"],
            "provenance": edge["provenance"],
        }

    relationship_evidence = pack["relationship_evidence"]

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
        "relationship_evidence": {
            "callers": [
                _serialize_relationship_edge(edge)
                for edge in relationship_evidence["callers"]
            ],
            "callees": [
                _serialize_relationship_edge(edge)
                for edge in relationship_evidence["callees"]
            ],
            "truncated": relationship_evidence["truncated"],
        },
        "caveats": pack["caveats"],
        "recommended_next_calls": pack["recommended_next_calls"],
    }


def handle_seam_plan(
    conn: sqlite3.Connection,
    root: Path,
    *,
    symbol: str | None = None,
    mode: str = "target",
    max_depth: int = 3,
    scope: str = _CHANGES_SCOPE_DEFAULT,
    base_ref: str = _CHANGES_BASE_REF_DEFAULT,
) -> dict[str, Any]:
    """Handler for the seam_plan tool.

    The planner composes existing static evidence into an inspect-and-test plan.
    It stays read-only and does not run tests, mutate git, or add graph evidence.
    """
    if mode == "target":
        if symbol is None or not symbol.strip():
            return _invalid_input("symbol must not be empty or whitespace-only")
        raw_plan = plan_target(conn, symbol.strip(), max_depth=max_depth)
    elif mode == "diff":
        if scope not in VALID_SCOPES:
            return _invalid_input(f"scope must be one of {sorted(VALID_SCOPES)}; got {scope!r}")
        if not base_ref or not base_ref.strip():
            return _invalid_input("base_ref must not be empty or whitespace-only")
        try:
            raw_plan = plan_diff(conn, repo_root=root, scope=scope, base_ref=base_ref.strip())
        except NotAGitRepoError as exc:
            return {"error": "NOT_A_GIT_REPO", "message": str(exc)}
    else:
        return _invalid_input("mode must be 'target' or 'diff'")

    result = _serialize_plan_result(raw_plan, root)
    result = _maybe_attach_staleness(result, conn, root)
    if result.get("index_status", {}).get("stale"):
        result.setdefault("caveats", []).append(
            "Index is stale; run seam sync before treating this plan as current."
        )
    return result


def _serialize_plan_result(plan: PlanResult, root: Path) -> dict[str, Any]:
    """Relativize DB-native planner paths at the transport boundary."""

    def rel(path: str | None) -> str | None:
        return _relativize(path, root) if path is not None else None

    def serialize_evidence(evidence: EvidenceRef) -> dict[str, Any]:
        item = dict(evidence)
        if "file" in item:
            item["file"] = rel(cast(str | None, item["file"]))
        return item

    def serialize_item(item: InspectionItem) -> dict[str, Any]:
        return {
            "symbol": item["symbol"],
            "file": rel(item["file"]),
            "line": item["line"],
            "kind": item["kind"],
            "reasons": item["reasons"],
            "tier": item["tier"],
            "confidence": item["confidence"],
            "evidence": [serialize_evidence(ev) for ev in item["evidence"]],
        }

    target = dict(plan.get("target", {}))
    if "file" in target:
        target["file"] = rel(target["file"])

    diff = dict(plan.get("diff", {}))
    if diff:
        diff["changed_symbols"] = [
            {
                **changed,
                "file": rel(changed.get("file")),
            }
            for changed in diff.get("changed_symbols", [])
        ]
        diff["new_files"] = [rel(path) for path in diff.get("new_files", [])]

    test_plan = dict(plan["test_plan"])
    test_files = [rel(path) for path in cast(list[str], test_plan["test_files"])]
    test_plan["test_files"] = test_files
    test_plan["commands"] = (
        [f"pytest {' '.join(path for path in test_files if path)}"]
        if test_files
        else []
    )

    result = {
        "mode": plan["mode"],
        "found": plan["found"],
        "risk": plan["risk"],
        "inspection_plan": [serialize_item(item) for item in plan["inspection_plan"]],
        "test_plan": test_plan,
        "caveats": plan["caveats"],
        "recommended_next_calls": plan["recommended_next_calls"],
        "omitted": plan["omitted"],
    }
    if target:
        result["target"] = target
    if diff:
        result["diff"] = diff
    return result


def handle_seam_schema(
    conn: sqlite3.Connection,
    root: Path,
    *,
    verbose: bool = False,
) -> dict[str, Any]:
    """Handler for the seam_schema MCP tool — read-only index capability map."""
    return describe_schema(conn, root=root, verbose=verbose)


def handle_seam_architecture(
    conn: sqlite3.Connection,
    root: Path,
    *,
    scope: str | None = None,
    sections: list[str] | None = None,
    limit: int = 10,
    max_bytes: int = 0,
) -> dict[str, Any]:
    """Handler for seam_architecture — thin adapter over the query module."""
    try:
        return describe_architecture(
            conn,
            root=root,
            scope=scope,
            sections=sections,
            limit=limit,
            max_bytes=max_bytes,
        )
    except ValueError as exc:
        return _invalid_input(str(exc))


def handle_seam_snippet(
    conn: sqlite3.Connection,
    root: Path,
    *,
    uid: str | None = None,
    symbol: str | None = None,
    file: str | None = None,
    line: int | None = None,
    context_lines: int = 0,
    max_lines: int = 200,
    max_bytes: int = 20_000,
    include_neighbors: bool = False,
) -> dict[str, Any]:
    """Keep transport handlers byte-identical by delegating selector rules to query.snippet."""
    return run_snippet(
        conn,
        root=root,
        uid=uid,
        symbol=symbol,
        file=file,
        line=line,
        context_lines=context_lines,
        max_lines=max_lines,
        max_bytes=max_bytes,
        include_neighbors=include_neighbors,
    )


def handle_seam_graph_search(
    conn: sqlite3.Connection,
    root: Path,
    *,
    kind: str | None = None,
    name_pattern: str | None = None,
    qualified_name_pattern: str | None = None,
    file_pattern: str | None = None,
    language: str | None = None,
    edge_kind: str | None = None,
    direction: str = "both",
    min_degree: int | None = None,
    max_degree: int | None = None,
    min_in_degree: int | None = None,
    max_in_degree: int | None = None,
    min_out_degree: int | None = None,
    max_out_degree: int | None = None,
    confidence: str | None = None,
    synthesized: str = "any",
    cluster_id: int | None = None,
    visibility: str | None = None,
    is_exported: bool | None = None,
    test_scope: str = "any",
    preset: str | None = None,
    sort: str = "default",
    limit: int = 20,
    offset: int = 0,
    include_preview: bool = False,
    preview_limit: int = 3,
    regex: bool = False,
    recipe: str | None = None,
) -> dict[str, Any]:
    """Delegate typed structural discovery to the transport-neutral query module."""
    return cast(dict[str, Any], run_graph_search(
        conn,
        root=root,
        kind=kind,
        name_pattern=name_pattern,
        qualified_name_pattern=qualified_name_pattern,
        file_pattern=file_pattern,
        language=language,
        edge_kind=edge_kind,
        direction=direction,  # type: ignore[arg-type]
        min_degree=min_degree,
        max_degree=max_degree,
        min_in_degree=min_in_degree,
        max_in_degree=max_in_degree,
        min_out_degree=min_out_degree,
        max_out_degree=max_out_degree,
        confidence=confidence,
        synthesized=synthesized,  # type: ignore[arg-type]
        cluster_id=cluster_id,
        visibility=visibility,
        is_exported=is_exported,
        test_scope=test_scope,  # type: ignore[arg-type]
        preset=preset,
        sort=sort,
        limit=limit,
        offset=offset,
        include_preview=include_preview,
        preview_limit=preview_limit,
        regex=regex,
        recipe=recipe,
    ))


def handle_seam_structure(
    conn: sqlite3.Connection,
    root: Path,
    *,
    path: Path | None = None,
    max_depth: int | None = None,
    max_nodes: int | None = None,
    include_functions: bool = False,
) -> StructureResult:
    """Handler for the seam_structure MCP tool — whole-repo structure tree.

    Returns a directory -> file -> container/function tree built from the index.
    Container nodes (class/interface/type) aggregate method/member rows into a
    `members` count rather than emitting separate child nodes. Top-level functions
    appear as 'function' children of their file node.

    File paths in the tree are relativized to `root` (no absolute paths leak).
    Container nodes carry path=None (they are logical, not file-backed).

    Slice 3 params:
      path:      When set, scopes the tree to this subdirectory.
      max_depth: Maximum nesting depth. None uses the config default.
      max_nodes: Maximum total non-root nodes. None uses the config default.

    This is a pure read; never raises — degrades to an empty safe tree on any error.

    Args:
        conn:      Open SQLite connection to the Seam index (read-only).
        root:      Project root Path — used to relativize file paths.
        path:      Optional scope path. Absolute paths are honoured as-is; a relative
                   path is resolved against `root` (NOT cwd) by build_structure.
        max_depth: Optional depth cap override.
        max_nodes: Optional node-count cap override.

    Returns:
        StructureResult dict with keys:
            tree:      Root 'dir' StructureNode representing `root` (or scoped path).
            truncated: Count of omitted nodes (0 when nothing was trimmed).
    """
    # Pass the scope path through unresolved: build_structure resolves a RELATIVE
    # path against `root` (not cwd), so MCP callers and the CLI get root-relative
    # scoping regardless of the server/process working directory.
    return run_build_structure(
        conn,
        root,
        path=path,
        max_depth=max_depth,
        max_nodes=max_nodes,
        include_functions=include_functions,
    )
