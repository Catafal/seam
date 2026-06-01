"""Tests for seam/query/clusters.py — read-layer for cluster queries.

TDD: Tests written before implementation.

Test groups:
    Q1 — list_clusters: returns cluster rows from seeded DB
    Q2 — cluster_members: returns members of a specific cluster
    Q3 — cluster_peers: returns (cluster_id, label, peer_names) for a symbol
    Q4 — Pre-v4 index: missing clusters table → empty results, no raise
    Q5 — Unknown cluster ID / symbol → empty results, no raise
"""

import sqlite3
from pathlib import Path

import pytest

# ── Helpers ───────────────────────────────────────────────────────────────────

def _seed_clustered_db() -> tuple[sqlite3.Connection, dict]:
    """Create an in-memory DB with two clusters seeded.

    Returns (conn, info) where info holds cluster IDs and member names.
    """
    from seam.indexer.db import init_db

    conn = init_db(Path(":memory:"))

    # Insert a file
    conn.execute(
        "INSERT INTO files (path, language, file_hash, mtime, indexed_at)"
        " VALUES ('/test.py', 'python', 'abc', 1.0, 1.0)"
    )
    file_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    # Insert two clusters
    conn.execute(
        "INSERT INTO clusters (label, size, naming_source) VALUES ('indexer/db — init_db', 2, 'deterministic')"
    )
    cluster_a_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    conn.execute(
        "INSERT INTO clusters (label, size, naming_source) VALUES ('query/engine — context', 1, 'deterministic')"
    )
    cluster_b_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    # Insert symbols assigned to clusters
    conn.execute(
        "INSERT INTO symbols (file_id, name, kind, start_line, end_line, cluster_id)"
        " VALUES (?, 'init_db', 'function', 1, 10, ?)",
        (file_id, cluster_a_id),
    )
    conn.execute(
        "INSERT INTO symbols (file_id, name, kind, start_line, end_line, cluster_id)"
        " VALUES (?, 'upsert_file', 'function', 11, 30, ?)",
        (file_id, cluster_a_id),
    )
    conn.execute(
        "INSERT INTO symbols (file_id, name, kind, start_line, end_line, cluster_id)"
        " VALUES (?, 'context', 'function', 31, 50, ?)",
        (file_id, cluster_b_id),
    )
    conn.commit()

    return conn, {
        "cluster_a_id": cluster_a_id,
        "cluster_b_id": cluster_b_id,
        "cluster_a_members": ["init_db", "upsert_file"],
        "cluster_b_members": ["context"],
    }


# ── Q1: list_clusters ─────────────────────────────────────────────────────────


class TestListClusters:
    """Q1: list_clusters returns all cluster rows."""

    def test_returns_list_of_clusters(self) -> None:
        """list_clusters returns a list with the expected number of clusters."""
        from seam.query.clusters import list_clusters

        conn, info = _seed_clustered_db()
        result = list_clusters(conn)
        assert isinstance(result, list)
        assert len(result) == 2

    def test_cluster_row_has_required_fields(self) -> None:
        """Each cluster row has id, label, size fields."""
        from seam.query.clusters import list_clusters

        conn, info = _seed_clustered_db()
        result = list_clusters(conn)
        for row in result:
            assert "id" in row, f"Missing 'id' in {row}"
            assert "label" in row, f"Missing 'label' in {row}"
            assert "size" in row, f"Missing 'size' in {row}"

    def test_cluster_sizes_are_correct(self) -> None:
        """list_clusters returns clusters with correct sizes."""
        from seam.query.clusters import list_clusters

        conn, info = _seed_clustered_db()
        result = list_clusters(conn)
        sizes = {row["id"]: row["size"] for row in result}

        assert sizes[info["cluster_a_id"]] == 2
        assert sizes[info["cluster_b_id"]] == 1

    def test_returns_empty_list_when_no_clusters(self) -> None:
        """Empty clusters table → empty list (not an error)."""
        from seam.indexer.db import init_db
        from seam.query.clusters import list_clusters

        conn = init_db(Path(":memory:"))
        result = list_clusters(conn)
        assert result == []


# ── Q2: cluster_members ───────────────────────────────────────────────────────


class TestClusterMembers:
    """Q2: cluster_members returns member symbols for a given cluster ID."""

    def test_returns_members_for_cluster(self) -> None:
        """cluster_members returns the correct members for cluster A."""
        from seam.query.clusters import cluster_members

        conn, info = _seed_clustered_db()
        members = cluster_members(conn, info["cluster_a_id"])
        assert isinstance(members, list)
        names = {m["name"] for m in members}
        assert names == {"init_db", "upsert_file"}

    def test_member_row_has_required_fields(self) -> None:
        """Each member row has name, file, line, kind fields."""
        from seam.query.clusters import cluster_members

        conn, info = _seed_clustered_db()
        members = cluster_members(conn, info["cluster_a_id"])
        for m in members:
            assert "name" in m
            assert "file" in m
            assert "line" in m
            assert "kind" in m

    def test_single_member_cluster(self) -> None:
        """Cluster B has exactly one member."""
        from seam.query.clusters import cluster_members

        conn, info = _seed_clustered_db()
        members = cluster_members(conn, info["cluster_b_id"])
        assert len(members) == 1
        assert members[0]["name"] == "context"


# ── Q3: cluster_peers ─────────────────────────────────────────────────────────


class TestClusterPeers:
    """Q3: cluster_peers returns (cluster_id, label, peer_names) for a symbol."""

    def test_returns_peers_for_symbol(self) -> None:
        """cluster_peers for 'init_db' returns 'upsert_file' as a peer."""
        from seam.query.clusters import cluster_peers

        conn, info = _seed_clustered_db()
        result = cluster_peers(conn, "init_db")

        assert result is not None
        cid, label, peers = result

        assert cid == info["cluster_a_id"]
        assert "upsert_file" in peers  # the other member
        assert "init_db" not in peers  # the queried symbol itself is excluded

    def test_returns_cluster_label(self) -> None:
        """cluster_peers returns the correct cluster label."""
        from seam.query.clusters import cluster_peers

        conn, info = _seed_clustered_db()
        result = cluster_peers(conn, "context")

        assert result is not None
        _cid, label, _peers = result
        assert "query/engine" in label or "context" in label

    def test_solo_member_returns_empty_peers(self) -> None:
        """The single-member cluster returns an empty peer list."""
        from seam.query.clusters import cluster_peers

        conn, info = _seed_clustered_db()
        result = cluster_peers(conn, "context")

        assert result is not None
        _cid, _label, peers = result
        assert peers == [], f"Solo member should have no peers, got {peers}"

    def test_returns_none_for_unknown_symbol(self) -> None:
        """cluster_peers for an unknown symbol returns None (not an error)."""
        from seam.query.clusters import cluster_peers

        conn, info = _seed_clustered_db()
        result = cluster_peers(conn, "nonexistent_symbol")
        assert result is None


# ── Q4: Pre-v4 index degrades gracefully ─────────────────────────────────────


class TestPreV4IndexGraceful:
    """Q4: When the clusters table is missing, all functions return [] / None."""

    def _make_pre_v4_conn(self) -> sqlite3.Connection:
        """Build a bare v3-style connection with NO clusters table."""
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript("""
            CREATE TABLE symbols (
                id INTEGER PRIMARY KEY, file_id INTEGER,
                name TEXT, kind TEXT, start_line INTEGER,
                end_line INTEGER, docstring TEXT
            );
            CREATE TABLE files (id INTEGER PRIMARY KEY, path TEXT);
        """)
        return conn

    def test_list_clusters_returns_empty_on_missing_table(self) -> None:
        """list_clusters on a pre-v4 index returns [] without raising."""
        from seam.query.clusters import list_clusters

        conn = self._make_pre_v4_conn()
        try:
            result = list_clusters(conn)
            assert result == []
        except Exception as exc:
            pytest.fail(f"list_clusters raised on missing table: {exc}")

    def test_cluster_members_returns_empty_on_missing_table(self) -> None:
        """cluster_members on a pre-v4 index returns [] without raising."""
        from seam.query.clusters import cluster_members

        conn = self._make_pre_v4_conn()
        try:
            result = cluster_members(conn, cluster_id=1)
            assert result == []
        except Exception as exc:
            pytest.fail(f"cluster_members raised on missing table: {exc}")

    def test_cluster_peers_returns_none_on_missing_column(self) -> None:
        """cluster_peers on a pre-v4 index returns None without raising."""
        from seam.query.clusters import cluster_peers

        conn = self._make_pre_v4_conn()
        try:
            result = cluster_peers(conn, "some_symbol")
            assert result is None
        except Exception as exc:
            pytest.fail(f"cluster_peers raised on missing column: {exc}")


# ── Q5: Unknown cluster/symbol → empty/None ───────────────────────────────────


class TestUnknownInputs:
    """Q5: Non-existent cluster IDs and symbol names return empty/None."""

    def test_cluster_members_unknown_id(self) -> None:
        """cluster_members for an ID that doesn't exist returns []."""
        from seam.indexer.db import init_db
        from seam.query.clusters import cluster_members

        conn = init_db(Path(":memory:"))
        result = cluster_members(conn, cluster_id=9999)
        assert result == []

    def test_cluster_peers_unknown_symbol(self) -> None:
        """cluster_peers for a symbol not in the index returns None."""
        from seam.indexer.db import init_db
        from seam.query.clusters import cluster_peers

        conn = init_db(Path(":memory:"))
        result = cluster_peers(conn, "not_there")
        assert result is None


# ── Q6: cluster_peers resolves the same symbol instance as context() ──────────


class TestClusterPeersSymbolResolution:
    """Q6: cluster_peers uses lowest-id resolution, consistent with context().

    Issue #5: if the first-instance (lowest id) of a name has cluster_id=NULL,
    cluster_peers must return None — not follow a higher-id row's cluster.
    """

    def _make_conn_with_null_first_instance(self) -> sqlite3.Connection:
        """Build a DB where 'helper' has two rows:
            id=low  → cluster_id=NULL (first instance, unclustered)
            id=high → cluster_id=1    (second instance, clustered)

        cluster_peers('helper') must return None because context() would resolve
        to the low-id row, which has no cluster.
        """
        from seam.indexer.db import init_db

        conn = init_db(Path(":memory:"))

        conn.execute(
            "INSERT INTO files (path, language, file_hash, mtime, indexed_at)"
            " VALUES ('/f.py', 'python', 'h', 1.0, 1.0)"
        )
        file_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        # Insert cluster row first (so we have an ID to reference)
        conn.execute(
            "INSERT INTO clusters (id, label, size, naming_source)"
            " VALUES (1, 'test cluster', 1, 'deterministic')"
        )

        # First 'helper' row: unclustered (NULL) — this is what context() picks
        conn.execute(
            "INSERT INTO symbols (file_id, name, kind, start_line, end_line, cluster_id)"
            " VALUES (?, 'helper', 'function', 1, 5, NULL)",
            (file_id,),
        )
        # Second 'helper' row: clustered — context() does NOT pick this one
        conn.execute(
            "INSERT INTO symbols (file_id, name, kind, start_line, end_line, cluster_id)"
            " VALUES (?, 'helper', 'function', 10, 15, 1)",
            (file_id,),
        )
        conn.commit()
        return conn

    def test_null_first_instance_returns_none(self) -> None:
        """cluster_peers returns None when the first-instance (lowest id) is unclustered."""
        from seam.query.clusters import cluster_peers

        conn = self._make_conn_with_null_first_instance()
        result = cluster_peers(conn, "helper")
        assert result is None, (
            "cluster_peers should return None when the first-instance row has cluster_id=NULL"
        )

    def test_clustered_first_instance_returns_peers(self) -> None:
        """cluster_peers returns the cluster tuple when the first-instance IS clustered."""
        from seam.indexer.db import init_db
        from seam.query.clusters import cluster_peers

        conn = init_db(Path(":memory:"))

        conn.execute(
            "INSERT INTO files (path, language, file_hash, mtime, indexed_at)"
            " VALUES ('/f.py', 'python', 'h', 1.0, 1.0)"
        )
        file_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        conn.execute(
            "INSERT INTO clusters (id, label, size, naming_source)"
            " VALUES (1, 'my cluster', 2, 'deterministic')"
        )
        # First 'alpha' row: clustered
        conn.execute(
            "INSERT INTO symbols (file_id, name, kind, start_line, end_line, cluster_id)"
            " VALUES (?, 'alpha', 'function', 1, 5, 1)",
            (file_id,),
        )
        # Peer 'beta' in same cluster
        conn.execute(
            "INSERT INTO symbols (file_id, name, kind, start_line, end_line, cluster_id)"
            " VALUES (?, 'beta', 'function', 6, 10, 1)",
            (file_id,),
        )
        conn.commit()

        result = cluster_peers(conn, "alpha")
        assert result is not None, "cluster_peers should return a tuple for a clustered first-instance"
        cid, label, peers = result
        assert cid == 1
        assert "beta" in peers
        assert "alpha" not in peers
