"""Read-only query layer for cluster data (Phase 2 — community detection).

Provides three entry points:
    list_clusters(conn)         → [{id, label, size}] — all clusters
    cluster_members(conn, id)   → [{name, file, line, kind}] — members of one cluster
    cluster_peers(conn, symbol) → (cluster_id, label, peer_names) | None

No server or CLI imports — pure query/read layer.
Callers (handler, CLI, engine) are responsible for path relativization.

Pre-v4 guard: when the clusters table or cluster_id column is missing
(index built before Phase 2), all functions return empty results + a one-time
warning. This mirrors the _comments_table_exists guard in query/comments.py.
"""

import logging
import sqlite3
from typing import TypedDict

logger = logging.getLogger(__name__)

# One-time warning state — avoids log spam when MCP server calls repeatedly
_pre_v4_warned = False


# ── Output TypedDicts ─────────────────────────────────────────────────────────


class ClusterRow(TypedDict):
    """One cluster summary row returned by list_clusters()."""
    id: int
    label: str
    size: int


class MemberRow(TypedDict):
    """One symbol member row returned by cluster_members()."""
    name: str
    file: str
    line: int
    kind: str


# ── Internal guards ───────────────────────────────────────────────────────────


def _clusters_table_exists(conn: sqlite3.Connection) -> bool:
    """Return True if the clusters table exists in this database.

    WHY: The MCP server opens a bare connect() (no schema script), so a pre-v4
    index won't have the clusters table. Querying it would raise OperationalError.
    We detect the missing table and degrade gracefully to empty results.
    """
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='clusters' LIMIT 1"
    ).fetchone()
    return row is not None


def _cluster_id_column_exists(conn: sqlite3.Connection) -> bool:
    """Return True if symbols.cluster_id column exists."""
    col_names = {row["name"] for row in conn.execute("PRAGMA table_info(symbols)").fetchall()}
    return "cluster_id" in col_names


def _warn_pre_v4_once() -> None:
    """Log a one-time warning when a pre-v4 index is detected."""
    global _pre_v4_warned
    if not _pre_v4_warned:
        logger.warning(
            "seam_clusters: clusters table or cluster_id column missing "
            "(index predates Phase 2 clustering) — run 'seam init' to populate. "
            "Returning empty results."
        )
        _pre_v4_warned = True


# ── Public API ────────────────────────────────────────────────────────────────


def list_clusters(conn: sqlite3.Connection) -> list[ClusterRow]:
    """Return all cluster summary rows.

    Args:
        conn: Open read-only SQLite connection.

    Returns:
        List of ClusterRow dicts sorted by id. Empty list when no clusters exist
        or when the index predates Phase 2 (pre-v4 index).
    """
    if not _clusters_table_exists(conn):
        _warn_pre_v4_once()
        return []

    rows = conn.execute(
        "SELECT id, label, size FROM clusters ORDER BY id"
    ).fetchall()

    return [ClusterRow(id=row["id"], label=row["label"], size=row["size"]) for row in rows]


def cluster_members(conn: sqlite3.Connection, cluster_id: int) -> list[MemberRow]:
    """Return the member symbols of a specific cluster.

    Args:
        conn:       Open read-only SQLite connection.
        cluster_id: The clusters.id value to look up.

    Returns:
        List of MemberRow dicts sorted by name. Empty list when the cluster
        doesn't exist or the index predates Phase 2.
    """
    if not _clusters_table_exists(conn) or not _cluster_id_column_exists(conn):
        _warn_pre_v4_once()
        return []

    rows = conn.execute(
        """
        SELECT s.name, f.path AS file, s.start_line AS line, s.kind
        FROM symbols s
        JOIN files f ON f.id = s.file_id
        WHERE s.cluster_id = ?
        ORDER BY s.name
        """,
        (cluster_id,),
    ).fetchall()

    return [
        MemberRow(
            name=row["name"],
            file=row["file"],
            line=row["line"],
            kind=row["kind"],
        )
        for row in rows
    ]


def cluster_peers(
    conn: sqlite3.Connection,
    symbol: str,
) -> tuple[int, str, list[str]] | None:
    """Return the cluster context for a symbol: (cluster_id, label, peer_names).

    Peers are all members of the same cluster EXCLUDING the queried symbol itself.

    Args:
        conn:   Open read-only SQLite connection.
        symbol: Symbol name to look up.

    Returns:
        (cluster_id, label, peer_names) if the symbol is clustered.
        None if the symbol is not in the index, has no cluster assignment,
        or the index predates Phase 2.
    """
    if not _clusters_table_exists(conn) or not _cluster_id_column_exists(conn):
        _warn_pre_v4_once()
        return None

    # Resolve the symbol the same way context() does: lowest id wins when ambiguous.
    # WHY (issue #5): if two rows share a name, the one with the lowest id may be
    # unclustered (cluster_id=NULL) while a higher-id row is clustered. Using a JOIN
    # on clusters would silently skip the NULL row and return the wrong definition's
    # cluster. We resolve the symbol first, THEN fetch the cluster info — consistent
    # with engine.py::context() which uses `ORDER BY s.id LIMIT 1` on a plain query.
    sym_row = conn.execute(
        "SELECT id, cluster_id FROM symbols WHERE name = ? ORDER BY id LIMIT 1",
        (symbol,),
    ).fetchone()

    if sym_row is None:
        # Symbol not in the index at all
        return None

    if sym_row["cluster_id"] is None:
        # Symbol exists but is unclustered (below min_size or no edges)
        return None

    cluster_id_val: int = sym_row["cluster_id"]

    # Fetch the cluster label
    cluster_row = conn.execute(
        "SELECT label FROM clusters WHERE id = ?",
        (cluster_id_val,),
    ).fetchone()

    if cluster_row is None:
        # Orphan cluster_id (shouldn't happen; be defensive)
        return None

    cluster_id: int = cluster_id_val
    label: str = cluster_row["label"]

    # Fetch all other members of the same cluster (peers)
    peer_rows = conn.execute(
        """
        SELECT DISTINCT s.name
        FROM symbols s
        WHERE s.cluster_id = ? AND s.name != ?
        ORDER BY s.name
        """,
        (cluster_id, symbol),
    ).fetchall()

    peers = [r["name"] for r in peer_rows]
    return cluster_id, label, peers
