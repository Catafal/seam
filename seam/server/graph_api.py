"""Neighborhood graph builder for the Seam Explorer visual API.

Exposed as: build_neighborhood(conn, symbol_name, direction) -> dict

The API contract (from .claude/tasks/seam-explorer-frontend.md):

    GET /api/graph/neighborhood?symbol=<name>&direction=both
    → { center, nodes: GraphNode[], edges: GraphEdge[] }

    GraphNode = {
        id=name, name, kind, signature, visibility, is_exported,
        cluster_id, cluster_label, definition_count
    }
    GraphEdge = {
        id, source=name, target=name,
        kind: "call"|"import",
        confidence: "EXTRACTED"|"AMBIGUOUS"|"INFERRED"
    }

Design notes:
- Node = symbol NAME (not file+name). Two files defining "helper" collapse to one
  node — consistent with the edges table which is name-keyed. This is the same
  homonym-collapse semantics used by seam_impact and seam_trace. Callers who need
  the per-definition detail use the /api/symbol/<name> endpoint (detail panel).
- Depth-1 only: we pull direct callers/callees from the edges table and stop.
  The client does lazy expansion (double-click a node) to explore further.
- Confidence is read directly from edges.confidence — no re-computation.
  The engine's confidence resolution (Phase 5 import-promotion) lives at query/engine
  time; the graph API trusts what was stored at index time, which is fine for
  the explorer's needs (visualisation, not blast-radius analysis).
- NEVER raises. Unknown symbol returns a safe empty envelope.

LAYER: seam.server (adapter layer) — may import from seam.query.* and seam.indexer.*
       but not from seam.cli.* or seam.server.mcp.*.
"""

import sqlite3
from typing import Any


def _fetch_center_node(
    conn: sqlite3.Connection,
    symbol_name: str,
) -> dict[str, Any] | None:
    """Fetch the center node's enrichment data from the symbols table.

    Returns a GraphNode dict if the symbol exists, else None.
    Aggregates across all definitions (homonym-collapse):
      - kind, signature, visibility, is_exported from the lowest-id row (consistent
        with engine.context() which uses ORDER BY s.id LIMIT 1)
      - definition_count from COUNT(*) over all rows with that name
      - cluster_id, cluster_label: from the first clustered row (or None)
    """
    row = conn.execute(
        """
        SELECT
            s.name,
            s.kind,
            s.signature,
            s.visibility,
            s.is_exported,
            s.cluster_id,
            c.label AS cluster_label,
            COUNT(*) OVER () AS definition_count
        FROM symbols s
        LEFT JOIN clusters c ON c.id = s.cluster_id
        WHERE s.name = ?
        ORDER BY s.id
        LIMIT 1
        """,
        (symbol_name,),
    ).fetchone()

    if row is None:
        return None

    # is_exported is stored as 0/1/NULL (SQLite has no native bool).
    raw_exp = row["is_exported"]
    is_exported: bool | None = None if raw_exp is None else bool(raw_exp)

    return {
        "id": row["name"],
        "name": row["name"],
        "kind": row["kind"],
        "signature": row["signature"],
        "visibility": row["visibility"],
        "is_exported": is_exported,
        "cluster_id": row["cluster_id"],
        "cluster_label": row["cluster_label"],
        "definition_count": row["definition_count"],
    }


def _fetch_neighbor_nodes(
    conn: sqlite3.Connection,
    symbol_names: set[str],
) -> dict[str, dict[str, Any]]:
    """Fetch enrichment data for a set of symbol names.

    Returns a dict keyed by name. Names not found in the symbols table are omitted
    (dangling edge targets — they won't appear as nodes). Each name collapses to
    one node exactly (homonym-collapse: definition_count reflects how many rows
    share that name, but all other fields come from the lowest-id row).

    WHY LEFT JOIN clusters: cluster data may not exist (pre-v4 index). The LEFT JOIN
    degrades cleanly — cluster_label becomes NULL, which maps to None in the output.
    """
    if not symbol_names:
        return {}

    placeholders = ",".join("?" * len(symbol_names))
    rows = conn.execute(
        f"""
        SELECT
            s.name,
            s.kind,
            s.signature,
            s.visibility,
            s.is_exported,
            s.cluster_id,
            c.label AS cluster_label,
            COUNT(*) OVER (PARTITION BY s.name) AS definition_count
        FROM symbols s
        LEFT JOIN clusters c ON c.id = s.cluster_id
        WHERE s.name IN ({placeholders})
        ORDER BY s.name, s.id
        """,
        list(symbol_names),
    ).fetchall()

    # Keep only the first (lowest-id) row per name — homonym-collapse.
    seen: set[str] = set()
    nodes: dict[str, dict[str, Any]] = {}
    for row in rows:
        name = row["name"]
        if name in seen:
            continue
        seen.add(name)
        raw_exp = row["is_exported"]
        is_exported: bool | None = None if raw_exp is None else bool(raw_exp)
        nodes[name] = {
            "id": name,
            "name": name,
            "kind": row["kind"],
            "signature": row["signature"],
            "visibility": row["visibility"],
            "is_exported": is_exported,
            "cluster_id": row["cluster_id"],
            "cluster_label": row["cluster_label"],
            "definition_count": row["definition_count"],
        }
    return nodes


def _fetch_edges(
    conn: sqlite3.Connection,
    symbol_name: str,
    direction: str,
) -> list[dict[str, Any]]:
    """Fetch depth-1 edges for a symbol from the edges table.

    direction:
        "callees" -> edges where source_name = symbol_name (symbol calls others)
        "callers" -> edges where target_name = symbol_name (others call symbol)
        "both"    -> union of the above (deduped by DB rowid)

    WHY read edges.confidence directly: the stored confidence was written at index
    time. For the explorer's visualization (solid/dashed/dotted lines), the stored
    value is the right signal. The Phase 5 import-promotion resolver adds provenance
    at query time but does not change the stored confidence — and this endpoint is
    purely visual, not blast-radius analysis.
    """
    if direction == "callees":
        sql = """
            SELECT id, source_name AS source, target_name AS target, kind, confidence
            FROM edges
            WHERE source_name = ?
        """
        params = [symbol_name]
    elif direction == "callers":
        sql = """
            SELECT id, source_name AS source, target_name AS target, kind, confidence
            FROM edges
            WHERE target_name = ?
        """
        params = [symbol_name]
    else:  # "both"
        # UNION deduplicates by (source, target, kind) at the SQL level; rowid
        # is taken from whichever branch first emits the row. This is fine for
        # the explorer — duplicate display edges would be confusing.
        sql = """
            SELECT id, source_name AS source, target_name AS target, kind, confidence
            FROM edges
            WHERE source_name = ?
            UNION
            SELECT id, source_name AS source, target_name AS target, kind, confidence
            FROM edges
            WHERE target_name = ?
        """
        params = [symbol_name, symbol_name]

    rows = conn.execute(sql, params).fetchall()
    return [
        {
            "id": row["id"],
            "source": row["source"],
            "target": row["target"],
            "kind": row["kind"],
            "confidence": row["confidence"],
        }
        for row in rows
    ]


def build_neighborhood(
    conn: sqlite3.Connection,
    symbol_name: str,
    direction: str = "both",
) -> dict[str, Any]:
    """Build a depth-1 neighborhood graph for a symbol.

    Returns:
        {
            "center": symbol_name,
            "nodes":  list[GraphNode],   # one entry per unique NAME (homonym-collapse)
            "edges":  list[GraphEdge],   # depth-1 only
        }

    GraphNode shape:
        id, name, kind, signature, visibility, is_exported,
        cluster_id, cluster_label, definition_count

    GraphEdge shape:
        id, source (name), target (name), kind ("call"|"import"),
        confidence ("EXTRACTED"|"AMBIGUOUS"|"INFERRED")

    Unknown symbol (not in DB) -> {center: name, nodes: [], edges: []}.
    Symbol with no edges -> {center: name, nodes: [center_node], edges: []}.

    Args:
        conn:        Open SQLite connection (read-only access patterns).
        symbol_name: The symbol name to center the graph on.
        direction:   "both" | "callers" | "callees". Defaults to "both".

    NEVER raises — bad input returns the safe empty envelope.
    """
    # Fetch center node enrichment.
    center_node = _fetch_center_node(conn, symbol_name)

    # Fetch depth-1 edges respecting direction filter.
    edges = _fetch_edges(conn, symbol_name, direction)

    if not edges and center_node is None:
        # Symbol unknown entirely — empty result.
        return {"center": symbol_name, "nodes": [], "edges": []}

    # Collect unique neighbor names from edges (excluding the center itself).
    neighbor_names: set[str] = set()
    for edge in edges:
        src, tgt = edge["source"], edge["target"]
        if src != symbol_name:
            neighbor_names.add(src)
        if tgt != symbol_name:
            neighbor_names.add(tgt)

    # Fetch enrichment data for neighbors.
    neighbor_nodes = _fetch_neighbor_nodes(conn, neighbor_names)

    # Build the nodes list: center first, then neighbors in deterministic order.
    nodes: list[dict[str, Any]] = []
    if center_node is not None:
        nodes.append(center_node)
    nodes.extend(neighbor_nodes[name] for name in sorted(neighbor_nodes))

    return {
        "center": symbol_name,
        "nodes": nodes,
        "edges": edges,
    }


def build_constellation(conn: sqlite3.Connection) -> dict[str, Any]:
    """Build the whole-repo cluster overview: clusters + inter-cluster links.

    Returns:
        {
            "clusters": [ {cluster_id, label, size} ],   # all clusters, largest first
            "links":    [ {source, target, weight} ],    # cross-cluster edge counts
        }

    `links` aggregates the `edges` table: every call/import whose source and target
    symbols live in DIFFERENT clusters contributes 1 to the (source_cid → target_cid)
    weight. Intra-cluster edges and edges touching an unclustered symbol are skipped.
    Direction is preserved (source cluster → target cluster).

    name→cluster mapping uses the lowest-id row per name (homonym-collapse, matching
    the name-keyed edges table): a name maps to exactly one cluster.

    Pre-v4 index (no clusters table) or empty index → {clusters: [], links: []}.
    NEVER raises — any DB error degrades to a safe (possibly partial) envelope.
    """
    # 1. Clusters. Guard the table itself (pre-v4 indexes have no clusters table).
    try:
        cluster_rows = conn.execute(
            "SELECT id, label, size FROM clusters ORDER BY size DESC, id"
        ).fetchall()
    except sqlite3.Error:
        return {"clusters": [], "links": []}

    clusters = [
        {"cluster_id": r["id"], "label": r["label"], "size": r["size"]}
        for r in cluster_rows
    ]
    if not clusters:
        return {"clusters": [], "links": []}

    # 2. name → cluster_id (lowest-id row per name). SQLite's bare-column rule binds
    #    cluster_id to the MIN(id) row, so each name resolves to one cluster.
    try:
        name_rows = conn.execute(
            "SELECT name, cluster_id, MIN(id) FROM symbols "
            "WHERE cluster_id IS NOT NULL GROUP BY name"
        ).fetchall()
    except sqlite3.Error:
        return {"clusters": clusters, "links": []}
    name_to_cid: dict[str, int] = {r["name"]: r["cluster_id"] for r in name_rows}

    # 3. Aggregate cross-cluster edge weights.
    weights: dict[tuple[int, int], int] = {}
    try:
        edge_rows = conn.execute("SELECT source_name, target_name FROM edges").fetchall()
    except sqlite3.Error:
        edge_rows = []
    for r in edge_rows:
        src_cid = name_to_cid.get(r["source_name"])
        tgt_cid = name_to_cid.get(r["target_name"])
        # Skip unclustered endpoints and intra-cluster edges (no inter-cluster signal).
        if src_cid is None or tgt_cid is None or src_cid == tgt_cid:
            continue
        key = (src_cid, tgt_cid)
        weights[key] = weights.get(key, 0) + 1

    # Heaviest links first (deterministic tie-break on the cluster id pair).
    links = [
        {"source": src, "target": tgt, "weight": weight}
        for (src, tgt), weight in sorted(weights.items(), key=lambda kv: (-kv[1], kv[0]))
    ]

    return {"clusters": clusters, "links": links}
