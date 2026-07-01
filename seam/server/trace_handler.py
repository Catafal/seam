"""handle_seam_trace handler — trace the shortest call/dependency path.

Extracted from seam/server/tools.py (Slice 2, P2 #103) as a pure mechanical split.
No logic change — byte-identical output before and after the extraction.

Import dependency: trace_handler → handler_common (one direction only, no cycle).
"""

import logging
import sqlite3
from pathlib import Path
from typing import Any

from seam.analysis import flows as flows_module
from seam.server.handler_common import (
    _TRACE_DEPTH_DEFAULT,
    _TRACE_DEPTH_MAX,
    _TRACE_DEPTH_MIN,
    _apply_verbosity,
    _clamp,
    _invalid_input,
    _maybe_attach_staleness,
    _qualified_trace_candidates,
    _resolve_uid,
    _serialize_edge_hop,
    _serialize_hop,
    _trace_not_found,
)

logger = logging.getLogger(__name__)


def handle_seam_trace(
    conn: sqlite3.Connection,
    source: str,
    target: str,
    root: Path,
    max_depth: int = _TRACE_DEPTH_DEFAULT,
    verbose: bool = True,
    *,
    uid: str | None = None,
    target_uid: str | None = None,
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
            from_name      (str)      — source of this edge
            to_name        (str)      — target of this edge
            kind           (str)      — full closed vocabulary: call | import | extends |
                                        implements | instantiates | holds | reads | writes |
                                        uses | http_calls | reads_config | configures
            confidence     (str)      — EXTRACTED | INFERRED | AMBIGUOUS
            synthesized_by (str|null) — E4: channel name for heuristic edges, null for static
                                        (present when SEAM_EDGE_PROVENANCE="on"; stripped in lean)

        Each EdgeHop dict:
            name           (str)      — neighboring symbol
            kind           (str)      — full closed vocabulary (same as Hop.kind above)
            confidence     (str)      — EXTRACTED | INFERRED | AMBIGUOUS
            synthesized_by (str|null) — E4: same semantics as Hop.synthesized_by above

    Error shapes:
        {"error": "INVALID_INPUT", "message": "..."} — blank source or target.
    """
    # uid / target_uid (P6c): stable handles pin the exact source/target symbols.
    # The path graph is name-keyed, so each uid resolves to its symbol NAME — the
    # handle just removes the disambiguation round-trip. An unknown uid yields the
    # standard "not connected" result (found=False), not an error.
    if uid is not None:
        resolved_src = _resolve_uid(conn, uid)
        if resolved_src is None:
            return _trace_not_found(uid, target_uid or target)
        source = resolved_src[0]
    if target_uid is not None:
        resolved_tgt = _resolve_uid(conn, target_uid)
        if resolved_tgt is None:
            return _trace_not_found(source, target_uid)
        target = resolved_tgt[0]

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
    paths = flows_module.trace(
        conn, clean_source, clean_target, max_depth=safe_depth, repo_root=root
    )
    resolved_source, resolved_target = clean_source, clean_target

    # Bare->qualified fallback (Tier D11): with Tier B receiver inference, method call
    # edges are stored QUALIFIED ('Class.method'), so a bare source/target matches no
    # edge `from_name`/`to_name` and trace returns nothing — an agent typing the natural
    # bare identifier gets found:false and must retry fully-qualified. ONLY on a genuine
    # miss (paths == []; self-trace returns [[]] and is left alone), resolve the bare
    # endpoints to their qualified symbol forms and retry the candidate pairs. Zero
    # regression: endpoints that already connect (top-level functions, qualified names)
    # never enter this branch.
    if not paths:
        src_cands = [clean_source, *_qualified_trace_candidates(conn, clean_source)]
        tgt_cands = [clean_target, *_qualified_trace_candidates(conn, clean_target)]
        for s in src_cands:
            for t in tgt_cands:
                if s == clean_source and t == clean_target:
                    continue  # the exact pair was already tried above
                cand = flows_module.trace(conn, s, t, max_depth=safe_depth, repo_root=root)
                if cand:
                    paths, resolved_source, resolved_target = cand, s, t
                    break
            if paths:
                break

    # Gather one-hop neighborhood for both symbols — useful context for the agent.
    # Use the RESOLVED endpoints so the neighborhood reflects the symbols the path
    # actually connected (identical to the inputs when no resolution was needed).
    # Thread repo_root so callers/callees also surface import-promotion provenance.
    callers_source = flows_module.callers(conn, resolved_source, repo_root=root)
    callees_source = flows_module.callees(conn, resolved_source, repo_root=root)
    callers_target = flows_module.callers(conn, resolved_target, repo_root=root)
    callees_target = flows_module.callees(conn, resolved_target, repo_root=root)

    # Serialize paths: each Path is list[Hop]; Hop is already a plain TypedDict.
    # Convert to plain dicts for JSON-safety.
    # Phase 5: include resolved_by for provenance (null when fast-path / not available).
    # Include best_candidate (relativized) for AMBIGUOUS hops.
    # Apply _apply_verbosity to each hop so lean mode strips resolved_by/best_candidate.
    serialized_paths = [
        [_apply_verbosity(_serialize_hop(hop, root), verbose) for hop in path] for path in paths
    ]

    trace_result: dict[str, Any] = {
        "found": len(paths) > 0,
        # Echo the RESOLVED endpoints so the agent sees what the path connected (e.g. a
        # bare 'bar' that resolved to 'Foo.bar'); identical to the input when unchanged.
        "source": resolved_source,
        "target": resolved_target,
        "paths": serialized_paths,
        "callers_source": [
            _apply_verbosity(_serialize_edge_hop(h, root), verbose) for h in callers_source
        ],
        "callees_source": [
            _apply_verbosity(_serialize_edge_hop(h, root), verbose) for h in callees_source
        ],
        "callers_target": [
            _apply_verbosity(_serialize_edge_hop(h, root), verbose) for h in callers_target
        ],
        "callees_target": [
            _apply_verbosity(_serialize_edge_hop(h, root), verbose) for h in callees_target
        ],
    }
    # P2: attach staleness banner LAST — purely additive, byte-identical when fresh.
    return _maybe_attach_staleness(trace_result, conn, root)
