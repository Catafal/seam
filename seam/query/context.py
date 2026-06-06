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
"""

import logging
import sqlite3

from seam.query.clusters import cluster_peers as _cluster_peers
from seam.query.names import edge_match_names as _edge_match_names

logger = logging.getLogger(__name__)


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

    cluster_info = _cluster_peers(conn, primary["name"])
    c_id, c_label, c_peers = cluster_info if cluster_info is not None else (None, None, [])
    decoded_decorators, is_exported = decode_enrichment_fields_fn(primary)

    return dict(
        symbol=primary["name"],
        file=primary["file"],
        line=primary["start_line"],
        end_line=primary["end_line"],
        kind=primary["kind"],
        docstring=primary["docstring"],
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
    # Slice 1 bridging: [qualified, bare] so call edges with bare target are found.
    callers, callees = collect_edges_for_names(conn, _edge_match_names(conn, symbol_name))
    cluster_info = _cluster_peers(conn, symbol_name)
    c_id, c_label, c_peers = cluster_info if cluster_info is not None else (None, None, [])
    decoded_decorators, is_exported = decode_enrichment_fields_fn(row)

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
    )
