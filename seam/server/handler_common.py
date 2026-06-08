"""Shared helpers and constants for all MCP tool handlers.

This module is a LEAF in the handler dependency tree:
  handler_common → analysis modules, query modules, config
  impact_handler → handler_common
  trace_handler  → handler_common
  tools          → handler_common, impact_handler, trace_handler

Extracted from seam/server/tools.py (Slice 2, P2 #103) as a pure mechanical split.
No logic change — byte-identical output before and after the extraction.

Contains:
  - _HEAVY_FIELDS, _apply_verbosity
  - Handler limit constants (query/search/impact/trace)
  - _serialize_hop, _serialize_edge_hop (used by trace handler + tests)
  - _trace_not_found, _qualified_trace_candidates (used by trace handler)
  - _relativize, _clamp
  - compute_uid, _resolve_uid (P6c stable handle)
  - _invalid_input, _invalid_query
  - _maybe_attach_staleness (P2 staleness banner helper)
"""

import hashlib
import logging
import sqlite3
from pathlib import Path
from typing import Any

import seam.config as config
from seam.analysis.flows import EdgeHop, Hop
from seam.analysis.staleness import StalenessVerdict, _watcher_is_alive, check_staleness
from seam.query.names import resolve_query_to_defs

logger = logging.getLogger(__name__)

# ── Lean-output: heavy fields stripped when verbose=False ────────────────────

# These fields are valuable in verbose mode but inflate every record unnecessarily
# when the agent only needs the core identity + signature.
# Keys are ABSENT (not null) in lean mode — lean mode's whole point is fewer bytes.
# E4: synthesized_by added — provenance detail, stripped in lean mode like resolved_by.
#     kind is NOT here — it is a core field always kept even in lean mode.
_HEAVY_FIELDS: frozenset[str] = frozenset(
    {
        "decorators",
        "is_exported",
        "visibility",
        "qualified_name",
        "resolved_by",
        "best_candidate",
        "synthesized_by",  # E4: provenance detail — stripped in lean, like resolved_by
    }
)


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
# Max bare->qualified candidates tried per trace endpoint on a path miss. Bounds the
# candidate-pair retry loop to CAP×CAP traces. A bare method name usually resolves to
# 1–2 qualified symbols; this caps the pathological common-name case ("run", "get").
_TRACE_ENDPOINT_CAND_CAP = 5


# ── Helpers ───────────────────────────────────────────────────────────────────


def _serialize_hop(hop: Hop, root: Path) -> dict[str, Any]:
    """Serialize a flows.Hop dict for JSON output with path relativization.

    Includes best_candidate (relativized) for AMBIGUOUS hops.

    E4: when SEAM_EDGE_PROVENANCE=on, adds 'synthesized_by' (the synthesis channel name
    when the hop is heuristic, null when statically extracted). synthesized_by is in
    _HEAVY_FIELDS and therefore stripped in lean mode (verbose=False) by the caller's
    _apply_verbosity call. 'kind' is always present (it was already emitted before E4).
    """
    raw_candidate: str | None = hop.get("best_candidate")
    record: dict[str, Any] = {
        "from_name": hop["from_name"],
        "to_name": hop["to_name"],
        "kind": hop["kind"],
        "confidence": hop["confidence"],
        "resolved_by": hop.get("resolved_by"),
        "best_candidate": _relativize(raw_candidate, root) if raw_candidate is not None else None,
    }
    # E4: surface synthesized_by when edge-provenance is enabled.
    # null (None) is retained — it is the common "static edge" value and is meaningful.
    if config.SEAM_EDGE_PROVENANCE == "on":
        record["synthesized_by"] = hop.get("synthesized_by")
    return record


def _serialize_edge_hop(hop: EdgeHop, root: Path | None = None) -> dict[str, Any]:
    """Serialize an EdgeHop TypedDict to a plain dict for JSON-safe output.

    Phase 5: includes resolved_by for provenance (null when not available).
    Includes best_candidate (relativized) for AMBIGUOUS hops.

    E4: when SEAM_EDGE_PROVENANCE=on, adds 'synthesized_by' (channel name for
    heuristic edges, null for static). In _HEAVY_FIELDS → stripped in lean mode.
    """
    raw_candidate = hop.get("best_candidate")
    record: dict[str, Any] = {
        "name": hop["name"],
        "kind": hop["kind"],
        "confidence": hop["confidence"],
        "resolved_by": hop.get("resolved_by"),  # Phase 5: null = unknown/fast-path
        # best_candidate for AMBIGUOUS hops; relativized when root is provided.
        "best_candidate": (
            _relativize(raw_candidate, root)
            if (raw_candidate is not None and root is not None)
            else raw_candidate
        ),
    }
    # E4: surface synthesized_by when edge-provenance is enabled.
    # null (None) is retained — it is the common "static edge" value and is meaningful.
    if config.SEAM_EDGE_PROVENANCE == "on":
        record["synthesized_by"] = hop.get("synthesized_by")
    return record


def _trace_not_found(source: str, target: str) -> dict[str, Any]:
    """Empty trace result for an unknown uid/target_uid (P6c).

    Mirrors the shape trace() produces for a genuinely-not-connected pair so an
    unknown handle reads as "no path", not as an error.
    """
    return {
        "found": False,
        "source": source,
        "target": target,
        "paths": [],
        "callers_source": [],
        "callees_source": [],
        "callers_target": [],
        "callees_target": [],
    }


def _qualified_trace_candidates(conn: sqlite3.Connection, name: str) -> list[str]:
    """Resolve a BARE trace endpoint to its qualified symbol form(s).

    WHY: with Tier B receiver inference, method call edges store qualified names
    ('Class.method') on both ends, so a bare endpoint matches no edge and trace
    returns nothing. This bridges bare -> qualified (the opposite direction from
    expand_impact_seeds, which bridges qualified -> bare for impact's upstream walk).

    Returns the qualified symbol names whose last dotted segment equals `name`
    (e.g. 'bar' -> ['Foo.bar']). Returns [] for an already-qualified name (the caller
    has already tried it) or when nothing resolves. Bounded by _TRACE_ENDPOINT_CAND_CAP
    to keep the candidate-pair retry loop small. Never raises.
    """
    if "." in name:
        return []  # already qualified — caller tried it directly
    try:
        rows = resolve_query_to_defs(conn, name)
    except Exception:  # noqa: BLE001 — read path never raises
        logger.debug("_qualified_trace_candidates: resolve failed for %r", name, exc_info=True)
        return []
    out: list[str] = []
    for row in rows:
        qn = row["name"]
        # Keep only the QUALIFIED forms (skip an exact bare match — already attempted).
        if qn != name and qn not in out:
            out.append(qn)
        if len(out) >= _TRACE_ENDPOINT_CAND_CAP:
            break
    return out


def _relativize(abs_path: str, root: Path) -> str:
    """Return abs_path relative to root; falls back to abs_path if not under root."""
    try:
        return str(Path(abs_path).relative_to(root))
    except ValueError:
        return abs_path


def _clamp(value: int, lo: int, hi: int) -> int:
    """Clamp value to [lo, hi] inclusive."""
    return max(lo, min(hi, value))


# ── Stable symbol UID handle (P6c) ────────────────────────────────────────────


def compute_uid(file_path: str, start_line: int) -> str:
    """Compute a stable, opaque handle for a symbol: sha1(file_path)[:8] + ':' + line.

    P6c: a homonym follow-up otherwise forces an agent to re-disambiguate by file
    path (an extra round-trip). The UID is a pure computed string — NO schema
    change, NO extra DB query. It is surfaced on search/query results and accepted
    as an alternative to `name` on context/impact/trace, where it resolves to the
    EXACT (file, line) symbol, bypassing homonym ambiguity.

    file_path is the ABSOLUTE path stored in the files table. We hash the absolute
    path (not the relativized output path) so the UID can be resolved back to the
    exact row by recomputing it over each candidate symbol's absolute path.
    """
    digest = hashlib.sha1(file_path.encode("utf-8")).hexdigest()[:8]
    return f"{digest}:{start_line}"


def _resolve_uid(conn: sqlite3.Connection, uid: str) -> tuple[str, str, int] | None:
    """Resolve a UID handle to the exact (name, abs_file_path, start_line) it pins.

    Strategy: a UID is sha1(abs_path)[:8] + ':' + start_line. The start_line is
    recoverable directly; the file prefix is NOT reversible, so we narrow by
    start_line in SQL (cheap — uses the line value) and recompute the UID for each
    candidate symbol at that line until one matches. This keeps the read path lean:
    no schema change, no O(files) scan — only the (typically tiny) set of symbols
    that begin at the same line is examined.

    Returns None for a malformed UID or one that matches no indexed symbol (the
    same not-found contract as an unknown symbol name).
    """
    prefix, sep, line_str = uid.partition(":")
    if not sep or not line_str.isdigit():
        return None
    start_line = int(line_str)

    rows = conn.execute(
        """
        SELECT s.name AS name, f.path AS file
        FROM symbols s
        JOIN files f ON f.id = s.file_id
        WHERE s.start_line = ?
        ORDER BY s.id
        """,
        (start_line,),
    ).fetchall()

    for row in rows:
        if compute_uid(row["file"], start_line) == uid:
            return row["name"], row["file"], start_line
    return None


def _invalid_input(message: str) -> dict[str, Any]:
    # Log so an operator can see what an agent actually sent (the error dict
    # otherwise vanishes into the agent's response with no server-side trace).
    logger.warning("rejected (INVALID_INPUT): %s", message)
    return {"error": "INVALID_INPUT", "message": message}


def _invalid_query(message: str) -> dict[str, Any]:
    logger.warning("rejected (INVALID_QUERY): %s", message)
    return {"error": "INVALID_QUERY", "message": message}


def _maybe_attach_staleness(
    response: dict[str, Any],
    conn: sqlite3.Connection,
    root: Path,
) -> dict[str, Any]:
    """Attach the P2 staleness banner to a graph-traversal handler response.

    Called as the LAST step in the 5 graph-traversal handlers (impact, changes,
    affected, context, trace). Purely additive — never alters existing fields.

    When SEAM_STALENESS_CHECK=off OR the index is fresh → returns response UNCHANGED
    (byte-identical to pre-feature). When stale → adds a top-level `index_status`
    key: {"stale": True, "reason": str, "hint": str}.

    WHY last step: staleness is orthogonal to the handler's core logic. Attaching
    it last keeps the core path clean and makes it easy to audit that only an
    additive field is added.

    Never raises — staleness check degrades gracefully on any IO error.
    """
    if config.SEAM_STALENESS_CHECK != "on":
        return response

    # Derive watcher-alive status from the standard PID file location.
    # The convention is root/.seam/watcher.pid (same as main.py start/status).
    pid_file = root / ".seam" / "watcher.pid"
    watcher_alive = _watcher_is_alive(pid_file) is not None

    verdict: StalenessVerdict = check_staleness(conn, root=root, watcher_alive=watcher_alive)
    if verdict["stale"]:
        # Additive only: build a new dict with index_status at the end.
        # WHY new dict: we never mutate the input (same contract as _apply_verbosity).
        result = dict(response)
        result["index_status"] = {
            "stale": True,
            "reason": verdict["reason"],
            "hint": verdict["hint"],
        }
        return result

    return response
