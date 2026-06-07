"""Context-building helpers extracted from engine.py (Tier A refactor).

LEAF-ADJACENT MODULE — imported by engine.py and tests only. Imports:
  - stdlib (sqlite3, json, logging)
  - seam.config
  - seam.query.names (edge bridging helpers)
  - seam.query.clusters (cluster peers)
  - seam.query.engine.decode_enrichment_fields (shared decoder)

WHY extracted from engine.py:
  engine.py exceeded the 1000-line hard limit after Tier A Slice 2 added
  _collect_edges_for_names, _build_merged_context_result, and _build_context_result.
  Moving these three helpers here keeps engine.py as the thin orchestrator and
  gives context() a dedicated home. No behavior change — pure refactor.

Context:
  Seam stores method symbol names as "Class.method" (qualified) but stores call-edge
  target_name as bare "method". These helpers bridge that asymmetry so callers/callees
  are correctly merged even when the edge key and the symbol key don't join directly.

A3 addition: build_context_result and build_merged_context_result now include
  field_readers / field_writers in the returned dict. These are populated by
  collect_field_access_for_names(), which queries edges WHERE kind IN ('reads','writes')
  targeting the symbol (for field seeds) or its member fields (for class seeds).
  For non-field, non-class seeds both lists are [].
"""

import logging
import sqlite3

from seam.query.clusters import cluster_peers as _cluster_peers
from seam.query.names import edge_match_names as _edge_match_names
from seam.query.names import get_member_names as _get_member_names
from seam.query.names import is_container_symbol as _is_container_symbol

logger = logging.getLogger(__name__)


def collect_field_access_for_names(
    conn: sqlite3.Connection,
    symbol_name: str,
    kind: str,
) -> list[sqlite3.Row] | list:
    """Return all edges of kind='reads' or 'writes' targeting the given symbol name.

    For a field seed 'Type.field': queries edges.target_name = 'Type.field' OR bare 'field'.
    Returns source_name values (the methods that read/write this field).
    Returns [] on error.

    WHY: field access edges store target_name as the qualified 'Type.field' (when the
    receiver type was inferred) OR as the bare 'field' (when the receiver was unresolvable).
    We query both forms to be consistent with how callers/callees edges are looked up.
    """
    if not symbol_name:
        return []
    match_names = _edge_match_names(conn, symbol_name)
    if not match_names:
        return []
    try:
        ph = ",".join("?" * len(match_names))
        rows = conn.execute(
            f"SELECT DISTINCT source_name FROM edges WHERE kind=? AND target_name IN ({ph})",
            [kind] + match_names,
        ).fetchall()
        return rows
    except Exception:  # noqa: BLE001
        logger.debug(
            "collect_field_access_for_names: DB error for %r kind=%r", symbol_name, kind,
            exc_info=True,
        )
        return []


def collect_field_access_split(
    conn: sqlite3.Connection,
    symbol_name: str,
    symbol_kind: str,
) -> tuple[list[str], list[str]]:
    """Return (field_readers, field_writers) for a symbol.

    For kind='field': returns functions that read/write this specific field.
    For kind='class'/'interface'/'type' (container): aggregates readers/writers across
      all member fields bounded by SEAM_NAME_EXPANSION_CAP.
    For all other kinds (function, method): returns ([], []).

    Never raises.
    """
    try:
        if symbol_kind == "field":
            # Direct field seed: query edges targeting this field.
            readers = sorted({
                r["source_name"]
                for r in collect_field_access_for_names(conn, symbol_name, "reads")
            })
            writers = sorted({
                r["source_name"]
                for r in collect_field_access_for_names(conn, symbol_name, "writes")
            })
            return readers, writers

        if _is_container_symbol(conn, symbol_name):
            # Class/interface/struct seed: aggregate across all member fields.
            # get_member_names returns bare member names; we also check qualified forms
            # via collect_field_access_for_names (which calls edge_match_names internally).
            member_names = _get_member_names(conn, symbol_name)
            # Build qualified field names from member names to query field-access edges.
            # member_names are bare (e.g. 'balance'); prefix with class_name to get
            # 'Account.balance' — the form stored as field symbols and read target.
            qualified_members = [f"{symbol_name}.{m}" for m in member_names]

            readers_set: set[str] = set()
            writers_set: set[str] = set()
            for qname in qualified_members:
                for r in collect_field_access_for_names(conn, qname, "reads"):
                    readers_set.add(r["source_name"])
                for r in collect_field_access_for_names(conn, qname, "writes"):
                    writers_set.add(r["source_name"])
            return sorted(readers_set), sorted(writers_set)

        # Non-field, non-class seeds: no readers/writers
        return [], []

    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "collect_field_access_split: failed for %r kind=%r: %r",
            symbol_name,
            symbol_kind,
            exc,
        )
        return [], []


def collect_edges_for_names(
    conn: sqlite3.Connection,
    match_names: list[str],
) -> tuple[set[str], set[str]]:
    """Return (callers_set, callees_set) for match_names via DISTINCT edge lookups.

    Used by both single-def (_build_context_result) and multi-def aggregation paths.
    Per-edge confidence from the DB is preserved — the union never invents confidence.

    Returns (set(), set()) for empty match_names or on DB error (defensive).
    """
    if not match_names:
        return set(), set()
    ph = ",".join("?" * len(match_names))
    try:
        # DISTINCT prevents duplicate caller/callee names when match_names contains
        # both the qualified form ("Class.method") and the bare form ("method") — both
        # can match the same edge row, so without DISTINCT the same caller appears twice.
        # The set comprehension dedups at Python level too, but DISTINCT reduces fetch size.
        callers = {
            r["source_name"]
            for r in conn.execute(
                f"SELECT DISTINCT source_name FROM edges WHERE target_name IN ({ph})",
                match_names,
            ).fetchall()
        }
        callees = {
            r["target_name"]
            for r in conn.execute(
                f"SELECT DISTINCT target_name FROM edges WHERE source_name IN ({ph})",
                match_names,
            ).fetchall()
        }
    except Exception:  # noqa: BLE001
        # Degrade gracefully — read path must never crash.
        logger.debug(
            "collect_edges_for_names: DB error for match_names=%r", match_names, exc_info=True
        )
        return set(), set()
    return callers, callees


def build_merged_context_result(
    conn: sqlite3.Connection,
    def_rows: list[sqlite3.Row],
    decode_enrichment_fields_fn,  # type: ignore[type-arg]
) -> dict:
    """Merge callers/callees across multiple defs (Slice 2 multi-def path).

    Primary def (lowest id) supplies location/kind/enrichment. ambiguous=True when >1
    def or exact-name collision; False for unique bare-name resolution (1 qualified def).

    decode_enrichment_fields_fn is passed in to avoid a circular import with engine.py
    (engine.py defines decode_enrichment_fields and is itself the caller here).
    """
    primary = def_rows[0]
    all_callers: set[str] = set()
    all_callees: set[str] = set()
    for row in def_rows:
        match_names = _edge_match_names(conn, row["name"])
        callers, callees = collect_edges_for_names(conn, match_names)
        all_callers |= callers
        all_callees |= callees

    # Cluster lookup uses the primary (lowest-id) def's name: for a bare-name resolution
    # like "parse" → "Parser.parse", the original bare name has no cluster row, whereas
    # the qualified name does. Using primary["name"] gives the right cluster for the
    # canonical representation in the index.
    cluster_info = _cluster_peers(conn, primary["name"])
    c_id, c_label, c_peers = cluster_info if cluster_info is not None else (None, None, [])
    decoded_decorators, is_exported = decode_enrichment_fields_fn(primary)

    # A3: field_readers / field_writers — populated for field and class seeds.
    field_readers, field_writers = collect_field_access_split(
        conn, primary["name"], primary["kind"]
    )

    return dict(
        symbol=primary["name"],
        file=primary["file"],
        line=primary["start_line"],
        end_line=primary["end_line"],
        kind=primary["kind"],
        docstring=primary["docstring"],
        # Sorted so MCP consumers get a deterministic list regardless of edge insertion order.
        callers=sorted(all_callers),
        callees=sorted(all_callees),
        ambiguous=len(def_rows) > 1,
        cluster_id=c_id,
        cluster_label=c_label,
        cluster_peers=c_peers,
        signature=primary["signature"],
        decorators=decoded_decorators,
        is_exported=is_exported,
        visibility=primary["visibility"],
        qualified_name=primary["qualified_name"],
        field_readers=field_readers,
        field_writers=field_writers,
    )


def build_context_result(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    *,
    dup_count: int,
    decode_enrichment_fields_fn,  # type: ignore[type-arg]
) -> dict:
    """Single symbol row → ContextResult dict. Used by context_at and the single-def fast path.

    dup_count: explicit collision count (required — no fallback to row['dup_count'] since
    resolve_query_to_defs rows do not include that window column).
    """
    symbol_name = row["name"]
    # A method stored as "Class.method" has call edges with target_name="method" (bare).
    # edge_match_names expands to [qualified, bare] so both storage forms are matched —
    # without this, callers of a qualified method always show up as an empty list.
    callers, callees = collect_edges_for_names(conn, _edge_match_names(conn, symbol_name))
    cluster_info = _cluster_peers(conn, symbol_name)
    c_id, c_label, c_peers = cluster_info if cluster_info is not None else (None, None, [])
    decoded_decorators, is_exported = decode_enrichment_fields_fn(row)

    # A3: field_readers / field_writers — populated for field and class seeds.
    field_readers, field_writers = collect_field_access_split(
        conn, symbol_name, row["kind"]
    )

    return dict(
        symbol=row["name"],
        file=row["file"],
        line=row["start_line"],
        end_line=row["end_line"],
        kind=row["kind"],
        docstring=row["docstring"],
        callers=sorted(callers),
        callees=sorted(callees),
        ambiguous=dup_count > 1,
        cluster_id=c_id,
        cluster_label=c_label,
        cluster_peers=c_peers,
        signature=row["signature"],
        decorators=decoded_decorators,
        is_exported=is_exported,
        visibility=row["visibility"],
        qualified_name=row["qualified_name"],
        field_readers=field_readers,
        field_writers=field_writers,
    )
