"""MCP tool handlers — thin adapters between MCP protocol and query engine.

Each handler: validates input → clamps limits → calls query.engine or analysis →
relativizes file paths → returns MCP-compatible response dict.

No business logic here. Query logic lives in seam/query/engine.py.
Impact logic lives in seam/analysis/impact.py.

Error conventions (matching mcp-tools.yaml):
  {"error": "INVALID_INPUT", "message": "..."} — blank/whitespace input
  {"error": "INVALID_QUERY", "message": "..."} — bad FTS5 syntax (seam_search only)
"""

import hashlib
import logging
import sqlite3
from pathlib import Path
from typing import Any

import seam.config as config
from seam.analysis import flows as flows_module
from seam.analysis import impact as impact_module
from seam.analysis.affected import AffectedResult
from seam.analysis.affected import affected as run_affected
from seam.analysis.byte_budget import fit_to_byte_budget, serialized_size
from seam.analysis.changes import (
    DEFAULT_BASE_REF,
    VALID_SCOPES,
    ChangeReport,
    NotAGitRepoError,
    detect_changes,
)
from seam.analysis.flows import EdgeHop, Hop
from seam.analysis.processes import Flow, build_flow, list_entry_points
from seam.analysis.relevance import order_by_relevance, owning_container, partition_self_refs
from seam.analysis.staleness import StalenessVerdict, _watcher_is_alive, check_staleness
from seam.analysis.steer import generate_steer
from seam.query import engine
from seam.query.clusters import cluster_members as query_cluster_members
from seam.query.clusters import list_clusters as query_list_clusters
from seam.query.comments import why as comments_why
from seam.query.names import get_member_names, is_container_symbol, resolve_query_to_defs
from seam.query.pack import ContextPack, NeighborRef
from seam.query.pack import context_pack as run_context_pack
from seam.query.structure import StructureResult
from seam.query.structure import build_structure as run_build_structure

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


# ── Handlers ──────────────────────────────────────────────────────────────────


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


def _serialize_tier_entry(
    entry: dict[str, Any],
    root: Path,
    verbose: bool,
    omit_null_candidate: bool = False,
) -> dict[str, Any]:
    """Serialize a single TieredEntry dict from the analysis layer.

    Relativizes file paths, includes Phase 5 provenance fields, and applies
    verbosity stripping. Extracted to keep the main handler readable.

    E1: when omit_null_candidate is True, the `best_candidate` key is DROPPED
    when its value is null. best_candidate is only meaningful for AMBIGUOUS
    entries; for EXTRACTED/INFERRED it is always null and carries no signal, so
    omitting it is lossless (null ≡ absent) and reclaims ~25 B/entry. In lean
    mode (_apply_verbosity already stripped it) this is a no-op.

    E4: when SEAM_EDGE_PROVENANCE=on, emits:
      - 'kind': the edge kind of the final hop (always present, NOT in _HEAVY_FIELDS
        because it is a core field kept in lean mode — like 'confidence').
      - 'synthesized_by': synthesis channel name when heuristic, null for static.
        In _HEAVY_FIELDS → stripped in lean mode (verbose=False), just like resolved_by.
        IMPORTANT: null is RETAINED in verbose mode (unlike best_candidate which is
        E1-omitted). For synthesized_by, null = "static edge", which is the common,
        informative case and must not be dropped.
    When SEAM_EDGE_PROVENANCE=off, neither 'kind' nor 'synthesized_by' is emitted →
    byte-identical pre-E4 output.
    """
    base: dict[str, Any] = {
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
    }

    # E4: emit edge provenance fields when the knob is on.
    # 'kind' is always kept (not in _HEAVY_FIELDS); 'synthesized_by' is in
    # _HEAVY_FIELDS and gets stripped by _apply_verbosity when verbose=False.
    if config.SEAM_EDGE_PROVENANCE == "on":
        base["kind"] = entry.get("kind", "")  # defensive: empty string for pre-E4 entries
        base["synthesized_by"] = entry.get("synthesized_by")  # null = static, retained

    record = _apply_verbosity(base, verbose)
    if omit_null_candidate and record.get("best_candidate") is None:
        record.pop("best_candidate", None)
    return record


def _prioritize_tier_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Order production (is_test=False) entries before test entries within a tier.

    Stable sort: preserves the analysis layer's BFS/distance order WITHIN the
    production and test groups (Python's sort is stable). Applied BEFORE the
    per-tier cap so that when the cap drops entries, production callers — what an
    agent assessing blast radius actually cares about — survive ahead of test
    dependents. WHY this matters: in a test-heavy repo a hub symbol's tier can be
    dominated by test callers (e.g. rescore had 52 test vs 9 production callers in
    LIKELY_AFFECTED), pushing the production callers past the cap of 25 and out of
    the default output entirely. Token budget is unchanged (still <= limit/tier).
    """
    return sorted(entries, key=lambda e: e.get("is_test", False))


def _compute_self_context(
    conn: sqlite3.Connection,
    target: str,
) -> tuple[str | None, set[str]]:
    """Resolve the target's container and own member-name set for self-ref ranking.

    Returns (container, self_names) where:
      - container  is the class/struct the target belongs to (the target itself when
        it IS a container, or its owning container when it's a method like "Foo.bar").
        None when the target is a free function / bare name with no container — such a
        target has no self-references and ordering falls back to production-before-test.
      - self_names is {container} ∪ {bare member names}. The owning_container() check in
        classify_self_ref handles qualified member entries ("Foo.bar"); self_names
        carries the container name and the BARE member entries ("bar") that
        owning_container() cannot resolve.

    WHY resolve the container even for a method target: querying impact on a single
    method "Foo.bar" should still surface EXTERNAL callers ahead of "Foo"'s other
    methods — those siblings live in the same file the developer is already editing,
    so they are low-signal self-references just like in the class-level case.

    Never raises (delegates to names.py helpers, which never raise).
    """
    if is_container_symbol(conn, target):
        container: str | None = target
    else:
        # Method ("Foo.bar") -> "Foo"; bare function ("run") -> None (no container).
        container = owning_container(target)

    if container is None:
        return None, set()

    members = get_member_names(conn, container)  # bare names, capped by config
    self_names = {container, *members}
    return container, self_names


def _shape_tier_group(
    tier_group: dict[str, list[dict[str, Any]]],
    root: Path,
    *,
    verbose: bool,
    effective_limit: int | None,
    relevance_on: bool,
    self_ref_mode: str,
    container: str | None,
    self_names: set[str],
    omit_null_candidate: bool = False,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, int], int]:
    """Order, cap, and serialize one direction's tier group (E2/E3 output shaping).

    Returns (capped_tiers, dir_truncated, dir_hidden_self_refs):
      - capped_tiers        — {tier: [serialized entries]} after ordering + cap.
      - dir_truncated       — {tier: count omitted by the per-tier cap}.
      - dir_hidden_self_refs — count of self-refs dropped in "hide" mode (else 0).

    Ordering runs BEFORE the cap so the cap sheds the lowest-relevance entries first.
    The analysis layer's ascending-distance order is preserved within each relevance
    group by the stable sort, so entries[:N] keeps the closest, highest-signal dependents.
    """
    capped_tiers: dict[str, list[dict[str, Any]]] = {}
    dir_truncated: dict[str, int] = {}
    dir_hidden_self_refs = 0

    for tier, entries in tier_group.items():
        if not relevance_on:
            # Relevance off: byte-identical revert to production-before-test.
            entries = _prioritize_tier_entries(entries)
        elif self_ref_mode == "hide":
            # Drop the target's own members entirely; count them; order the remaining
            # externals production-before-test. risk_summary (counted by the caller
            # before this) still includes self-refs, so the blast radius stays honest —
            # the dropped members surface as hidden_self_refs.
            external, self_refs = partition_self_refs(entries, container, self_names)
            dir_hidden_self_refs += len(self_refs)
            entries = order_by_relevance(external, container, self_names)
        else:
            # "rank" (default) or "show": keep everything, externals/production first
            # and self-references last (so the cap sheds them first).
            entries = order_by_relevance(entries, container, self_names)

        if effective_limit is not None and len(entries) > effective_limit:
            kept = entries[:effective_limit]
            dir_truncated[tier] = len(entries) - effective_limit
        else:
            kept = entries
            dir_truncated[tier] = 0

        # Serialize each kept entry: relativize paths + apply verbose stripping +
        # E1 null-best_candidate omission.
        capped_tiers[tier] = [
            _serialize_tier_entry(entry, root, verbose, omit_null_candidate) for entry in kept
        ]

    return capped_tiers, dir_truncated, dir_hidden_self_refs


# Worst-case size (chars) of the trailing `truncated` structure the byte pass can add
# on top of what the count cap already wrote: 6 (direction × tier) slots in the CLI emit
# serialization. Reserved (with the exact byte_capped size) so the FINAL response —
# entries PLUS the trailing byte_capped/truncated metadata — still fits the budget,
# making the ceiling a hard guarantee rather than entries-only.
_BYTE_CEILING_TRUNCATED_RESERVE = 200


def _count_direction_entries(response: dict[str, Any]) -> int:
    """Total entries across all direction-tier lists — the upper bound on `omitted`."""
    total = 0
    for direction in ("upstream", "downstream"):
        dir_group = response.get(direction)
        if isinstance(dir_group, dict):
            for tier_val in dir_group.values():
                if isinstance(tier_val, list):
                    total += len(tier_val)
    return total


def _apply_byte_ceiling(
    response: dict[str, Any], budget: int, *, extra_reserve: int = 0
) -> dict[str, Any]:
    """Apply the E1-FULL byte ceiling to a fully-assembled seam_impact response.

    Runs AFTER the per-tier count cap and E2/E3 relevance ordering. When budget > 0 and
    the response does not already fit, trims entries (via fit_to_byte_budget) from the
    least-valuable end, merges the byte-dropped counts into response["truncated"]
    additively (so risk_summary - shown == truncated holds end-to-end), and sets
    response["byte_capped"] = {"limit", "omitted"}.

    Hard-ceiling guarantee: the trim runs against `budget - reserve`, where `reserve`
    is the exact byte_capped size plus a worst-case allowance for the `truncated` growth
    this function appends afterwards — so the FINAL serialized response (entries + that
    trailing metadata) stays within `budget`. The only exception is a `budget` smaller
    than the irreducible envelope, where no entries fit at all.

    When budget <= 0: returns the response unchanged (byte-identical revert).
    When the response already fits: returns it unchanged, byte_capped NOT added (so a
    generous budget is a true no-op).

    Never raises — the whole body is guarded; on any failure the untrimmed response is
    returned. If the trim degrades to a no-op despite the response NOT fitting (the leaf's
    never-raises safety net fired), that silent path is logged so it is observable.

    Args:
        response: Fully-assembled seam_impact response dict (non-mutating).
        budget:   SEAM_IMPACT_MAX_BYTES (from param). 0 or negative = unlimited.
        extra_reserve: Additional bytes to hold back from the trim budget (E4). The
                  handler passes the serialized size of the `next_actions` steer here so
                  the FINAL response — entries + byte_capped/truncated + next_actions —
                  still fits `budget`. byte_capped["limit"] keeps reporting the true
                  `budget` (not the reduced trim budget), so the reported ceiling is honest.

    Returns:
        The (possibly trimmed) response dict with merged truncated + byte_capped.
    """
    if budget <= 0:
        return response

    try:
        # Already within budget (including the steer the handler will append) → no trim.
        if serialized_size(response) + extra_reserve <= budget:
            return response

        # Reserve room for the trailing metadata the merge appends, so the final
        # response still fits. byte_capped is sized exactly (omitted <= entry count);
        # truncated growth uses a worst-case 6-slot allowance. extra_reserve (E4) holds
        # back room for the next_actions steer the handler appends after this returns.
        total_entries = _count_direction_entries(response)
        reserve = serialized_size({"byte_capped": {"limit": budget, "omitted": total_entries}})
        reserve += _BYTE_CEILING_TRUNCATED_RESERVE + max(extra_reserve, 0)
        effective = max(budget - reserve, 1)

        trimmed, byte_dropped, total_omitted = fit_to_byte_budget(response, budget=effective)

        if total_omitted == 0:
            # The response did NOT fit (checked above) yet nothing was trimmed — the
            # leaf's never-raises safety net fired. Surface it and return untrimmed
            # rather than attach a misleading byte_capped that claims a trim happened.
            logger.warning(
                "seam_impact byte ceiling could not trim output (budget=%d); returning untrimmed",
                budget,
            )
            return response

        # Merge byte_dropped into truncated ADDITIVELY so the invariant holds:
        #   risk_summary[dir][tier] - shown[dir][tier] == truncated[dir][tier]
        existing_truncated: dict[str, dict[str, int]] = dict(trimmed.get("truncated", {}))
        for direction, tier_map in byte_dropped.items():
            dir_trunc = dict(existing_truncated.get(direction, {}))
            for tier, count in tier_map.items():
                dir_trunc[tier] = dir_trunc.get(tier, 0) + count
            existing_truncated[direction] = dir_trunc

        # trimmed is a new dict from fit_to_byte_budget (never mutates input).
        result = dict(trimmed)
        result["truncated"] = existing_truncated
        result["byte_capped"] = {"limit": budget, "omitted": total_omitted}
        return result
    except Exception:
        # The handler claims "never raises" in its own right (not only via the leaf).
        logger.warning("seam_impact byte ceiling failed; returning untrimmed output", exc_info=True)
        return response


def handle_seam_impact(
    conn: sqlite3.Connection,
    target: str,
    root: Path,
    direction: str = _IMPACT_DIRECTION_DEFAULT,
    max_depth: int = _IMPACT_DEPTH_DEFAULT,
    include_tests: bool = False,
    verbose: bool = True,
    limit: int = config.SEAM_IMPACT_MAX_RESULTS,
    max_bytes: int = config.SEAM_IMPACT_MAX_BYTES,
    *,
    uid: str | None = None,
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
        include_tests: When False (default), test-file dependents are filtered out from
                       all tiers — "what breaks?" answers with the PRODUCTION blast radius,
                       and the count of hidden test dependents surfaces as `hidden_tests`.
                       When True, test-file dependents are included and tagged is_test=True.
                       (Test dependents are derivable separately via seam_affected.)
        verbose:       When True (default), output includes all Phase 4/5 enrichment fields.
                       When False, heavy fields (resolved_by, best_candidate, etc.) are
                       stripped from each entry — lean mode.
        limit:         Per-tier entry cap. Default: SEAM_IMPACT_MAX_RESULTS (25).
                       Entries arrive distance-ordered from the analysis layer (tiers group
                       by distance), so the kept slice is always the closest/highest-risk.
                       limit <= 0 means unlimited (all entries returned).
        max_bytes:     Optional byte ceiling for the serialized output (characters of compact
                       JSON). Default: SEAM_IMPACT_MAX_BYTES (0 = unlimited). When > 0, the
                       ceiling runs AFTER the per-tier count cap and E2/E3 ordering, trimming
                       entries from the least-valuable end (downstream before upstream,
                       MAY_NEED_TESTING before WILL_BREAK, tail before front) until the
                       serialized output fits. The dropped counts are merged into `truncated`
                       additively and a `byte_capped` key is added when the ceiling fired
                       (byte_capped is ABSENT when max_bytes=0 or nothing was trimmed). 0 or
                       negative means unlimited — byte-identical to the pre-feature output.

    Returns:
        A JSON-able dict with the impact result, or an error dict on bad input.
        Top-level keys always include `found`, `target`, and `risk_summary`.
        risk_summary is {direction: {tier: count}} computed from the FULL pre-cap
        result — it is always trustworthy even when entry lists are capped.
        NOTE: "full" means before the `limit` cap, but AFTER the include_tests filter —
        when include_tests=False, risk_summary counts the production-only blast radius
        (test dependents are already excluded), matching the entries actually returned.
        When any tier was capped, `truncated` is included: {direction: {tier: omitted}}.
        When the byte ceiling fires, `byte_capped` is added: {"limit": int, "omitted": int}.

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
    # uid (P6c): a stable handle pins the exact symbol. The impact graph is
    # name-keyed (edges store names), so we resolve the uid to its symbol NAME and
    # analyze that — the handle just removes the homonym disambiguation round-trip.
    # An unknown uid returns the standard found=False result (not an error).
    if uid is not None:
        resolved = _resolve_uid(conn, uid)
        if resolved is None:
            return {"found": False, "target": uid, "risk_summary": {}}
        target = resolved[0]

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

    # E2/E3 output shaping (handler-layer only — seam_changes/seam_affected bypass this).
    # relevance_on ranks EXTERNAL dependents ahead of the target's own members so the
    # per-tier cap drops self-references first. self_ref_mode "hide" additionally drops
    # self-refs entirely and surfaces hidden_self_refs (mirrors hidden_tests).
    relevance_on = config.SEAM_IMPACT_RELEVANCE_SORT == "on"
    self_ref_mode = config.SEAM_IMPACT_SELF_REF
    # E1: drop null best_candidate per entry (lossless; null ≡ absent) to keep the
    # default output lean so more high-signal dependents survive the per-tier cap.
    omit_null_candidate = config.SEAM_IMPACT_OMIT_NULL_CANDIDATE == "on"
    # Resolve the self-ref context only when it can actually change ordering — i.e.
    # relevance is on and the mode treats self-refs specially ("rank"/"hide"). "show"
    # and relevance-off skip the lookup (container=None → no entry is a self-ref).
    if relevance_on and self_ref_mode in ("rank", "hide"):
        container, self_names = _compute_self_context(conn, target.strip())
    else:
        container, self_names = None, set()
    hidden_self_refs = 0

    # Build risk_summary and capped tiers for each direction key present in raw.
    # WHY compute summary first: risk_summary must reflect the FULL pre-cap result
    # (story 15) — we count before slicing so truncation cannot hide the true total.
    # In "hide" mode the summary still counts self-refs (the honest total); the dropped
    # self-refs surface separately as hidden_self_refs.
    risk_summary: dict[str, dict[str, int]] = {}
    truncated: dict[str, dict[str, int]] = {}

    for dir_key in ("upstream", "downstream"):
        if dir_key not in raw:
            continue
        tier_group = raw[dir_key]

        # ── 1. Count BEFORE capping (risk_summary denominator) ────────────────
        # Counts the FULL pre-cap tier group including self-refs (the honest total).
        dir_summary = {tier: len(entries) for tier, entries in tier_group.items()}
        risk_summary[dir_key] = dir_summary

        # ── 2. Order (E2/E3) + per-tier cap + serialize ───────────────────────
        capped_tiers, dir_truncated, dir_hidden = _shape_tier_group(
            tier_group,
            root,
            verbose=verbose,
            effective_limit=effective_limit,
            relevance_on=relevance_on,
            self_ref_mode=self_ref_mode,
            container=container,
            self_names=self_names,
            omit_null_candidate=omit_null_candidate,
        )
        hidden_self_refs += dir_hidden
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
    # were hidden" — without it, the production-only default could read as a false-safe.
    if "hidden_tests" in raw:
        response["hidden_tests"] = raw["hidden_tests"]

    # Surface hidden_self_refs whenever hide mode is active (even when 0), so agents
    # can rely on its presence to reconcile risk_summary against the shown entries.
    if relevance_on and self_ref_mode == "hide":
        response["hidden_self_refs"] = hidden_self_refs

    # E1-FULL: byte ceiling — runs LAST (before steer), after count cap + E2/E3 ordering.
    # When max_bytes > 0, trims entries from the least-valuable end until the
    # serialized output fits the budget. byte_capped is set only when the ceiling
    # actually fired (i.e. at least one entry was dropped). When max_bytes <= 0
    # this is a no-op (byte-identical revert). seam_changes/seam_affected bypass
    # this entirely because they call the analysis layer directly.
    final_response = _apply_byte_ceiling(response, max_bytes)

    # E4: truncation steer — runs AFTER byte ceiling so it reads the merged truncated
    # totals (count-cap drops + byte-ceiling drops) and the byte_capped metadata.
    # Generates ready-to-act prose hints when ≥1 entry was trimmed. ABSENT when
    # nothing was trimmed (so presence is an unambiguous "there is more" signal).
    # Gated by SEAM_IMPACT_STEER; "off" = byte-identical pre-E4 (no next_actions key).
    # `response` (pre-ceiling) is passed so the steer-aware re-trim starts clean rather
    # than re-trimming an already-trimmed response (which would double-count truncated).
    if config.SEAM_IMPACT_STEER == "on":
        final_response = _attach_steer(
            final_response, response, limit=limit, max_bytes=max_bytes
        )

    # P2: attach staleness banner LAST — purely additive, byte-identical when fresh.
    return _maybe_attach_staleness(final_response, conn, root)


# Small margin (chars) added to the steer-byte reserve when re-trimming so the
# regenerated steer's digit growth (byte-drop counts grow as more entries are trimmed)
# cannot nudge the response back over the budget.
_STEER_RESERVE_MARGIN = 64


def _attach_steer(
    final_response: dict[str, Any],
    pre_ceiling_response: dict[str, Any],
    *,
    limit: int,
    max_bytes: int,
) -> dict[str, Any]:
    """Generate the E4 next_actions steer and attach it WITHIN the byte ceiling (E4 fix).

    The steer is generated from the post-ceiling trim metadata. Naively appending it would
    push the response past max_bytes — defeating the E1-FULL hard ceiling exactly when the
    ceiling fired (the steer fires iff something was trimmed). So when max_bytes is active
    and attaching the steer would breach the budget, we re-run the ceiling from the
    PRE-CEILING response (clean — not the already-trimmed one, which would double-count
    `truncated`), reserving room for the steer, then regenerate it for the smaller set.

    WHY a single re-trim converges: the steer's count-cap hints depend only on the
    count-cap portion of `truncated` (applied before the ceiling), which is INVARIANT
    under further byte trimming. So the regenerated steer differs from the first only in
    the byte-hint's trailing count — a few digits — absorbed by _STEER_RESERVE_MARGIN.
    No iteration loop needed.

    All-trimmed (budget-below-envelope) is the documented exception: entries are already
    empty, re-trimming changes nothing, and the anti-false-safe WARNING is the point — it
    is attached even if it exceeds a sub-envelope budget (the same carve-out the
    irreducible envelope already has).

    tier_order / direction_order are injected from impact.py's canonical TIER_* constants
    so the steer has a single source of truth for the tier names (no hardcoded copy).
    """
    tier_order = (
        impact_module.TIER_WILL_BREAK,
        impact_module.TIER_LIKELY_AFFECTED,
        impact_module.TIER_MAY_NEED_TESTING,
    )

    def _make_steer(resp: dict[str, Any]) -> list[str]:
        return generate_steer(
            truncated=resp.get("truncated", {}),
            byte_capped=resp.get("byte_capped"),
            risk_summary=resp.get("risk_summary", {}),
            limit=limit,
            max_bytes=max_bytes,
            tier_order=tier_order,
            direction_order=("upstream", "downstream"),
        )

    steer = _make_steer(final_response)
    if not steer:
        return final_response

    # Keep the steer inside the byte budget (E4 STOP fix). Only re-trim when the budget
    # is active AND attaching the steer would actually breach it.
    if max_bytes > 0:
        steer_bytes = serialized_size({"next_actions": steer})
        if serialized_size(final_response) + steer_bytes > max_bytes:
            # Re-trim from the PRE-ceiling response (clean single pass) reserving room
            # for the steer, then regenerate the steer for the now-smaller entry set.
            final_response = _apply_byte_ceiling(
                pre_ceiling_response,
                max_bytes,
                extra_reserve=steer_bytes + _STEER_RESERVE_MARGIN,
            )
            steer = _make_steer(final_response)
            if not steer:
                return final_response

    final_response["next_actions"] = steer
    return final_response


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
                                        implements | instantiates | holds | reads | writes | uses
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
