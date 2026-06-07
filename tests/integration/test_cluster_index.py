"""Integration tests for seam/indexer/cluster_index.py — clustering orchestration.

Tests the full pipeline: read graph from DB → detect → label → persist.

Test groups:
    CI1 — Small seeded DB: clusters are written to clusters table + symbols.cluster_id
    CI2 — Empty DB (no symbols/edges): leaves tables empty without error
    CI3 — Graph with clear communities: produces expected cluster count
    CI4 — Error resilience: clustering errors leave cluster_id NULL, do not abort
    CI5 — Deterministic IDs: same graph twice → identical cluster ids
    CI6 — min_size enforcement: communities below min_size get cluster_id=NULL
    CI7 — size accuracy: clusters.size matches actual symbols.cluster_id count
    CI8 — Orphan clearance: clustering empty set after populated run leaves no ghosts
    CI9 — Failure signal: index_clusters returns -1 on error (not 0)
"""

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

# ── Helpers ───────────────────────────────────────────────────────────────────

def _seed_db(conn: sqlite3.Connection, symbols: list[dict], edges: list[dict]) -> None:
    """Seed a DB with test symbols and edges.

    symbols: list of {"name": str, "kind": str}
    edges: list of {"source": str, "target": str}
    """
    # Insert a placeholder file
    conn.execute(
        "INSERT INTO files (path, language, file_hash, mtime, indexed_at)"
        " VALUES ('/test.py', 'python', 'abc', 1.0, 1.0)"
    )
    file_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    for sym in symbols:
        conn.execute(
            "INSERT INTO symbols (file_id, name, kind, start_line, end_line)"
            " VALUES (?, ?, ?, 1, 1)",
            (file_id, sym["name"], sym.get("kind", "function")),
        )

    for edge in edges:
        conn.execute(
            "INSERT INTO edges (source_name, target_name, kind, file_id, line, confidence)"
            " VALUES (?, ?, 'call', ?, 1, 'INFERRED')",
            (edge["source"], edge["target"], file_id),
        )
    conn.commit()


# ── CI1: Clusters written to DB ───────────────────────────────────────────────


class TestClusterIndexWritesToDB:
    """CI1: After index_clusters(), clusters table and symbols.cluster_id are populated."""

    def test_clusters_table_populated(self) -> None:
        """index_clusters() inserts rows into clusters table."""
        from seam.indexer.cluster_index import index_clusters
        from seam.indexer.db import init_db

        conn = init_db(Path(":memory:"))

        # Two clear communities: a1/a2/a3 tightly connected, b1/b2 tightly connected
        symbols = [
            {"name": "a1"}, {"name": "a2"}, {"name": "a3"},
            {"name": "b1"}, {"name": "b2"},
        ]
        edges = [
            {"source": "a1", "target": "a2"},
            {"source": "a2", "target": "a3"},
            {"source": "a1", "target": "a3"},
            {"source": "b1", "target": "b2"},
            # Weak link between communities
            {"source": "a1", "target": "b1"},
        ]
        _seed_db(conn, symbols, edges)

        index_clusters(conn, naming_mode="deterministic")

        cluster_count = conn.execute("SELECT COUNT(*) FROM clusters").fetchone()[0]
        assert cluster_count > 0, "Expected clusters to be inserted into clusters table"

    def test_symbols_have_cluster_id_after_indexing(self) -> None:
        """After index_clusters(), all symbols have a non-NULL cluster_id."""
        from seam.indexer.cluster_index import index_clusters
        from seam.indexer.db import init_db

        conn = init_db(Path(":memory:"))
        symbols = [{"name": "fn1"}, {"name": "fn2"}, {"name": "fn3"}]
        edges = [{"source": "fn1", "target": "fn2"}, {"source": "fn2", "target": "fn3"}]
        _seed_db(conn, symbols, edges)

        index_clusters(conn, naming_mode="deterministic")

        null_count = conn.execute(
            "SELECT COUNT(*) FROM symbols WHERE cluster_id IS NULL"
        ).fetchone()[0]
        assert null_count == 0, f"{null_count} symbols still have NULL cluster_id after clustering"

    def test_cluster_rows_have_required_fields(self) -> None:
        """Cluster rows have label, size, naming_source."""
        from seam.indexer.cluster_index import index_clusters
        from seam.indexer.db import init_db

        conn = init_db(Path(":memory:"))
        symbols = [{"name": "fn1"}, {"name": "fn2"}]
        edges = [{"source": "fn1", "target": "fn2"}]
        _seed_db(conn, symbols, edges)

        index_clusters(conn, naming_mode="deterministic")

        rows = conn.execute("SELECT label, size, naming_source FROM clusters").fetchall()
        assert len(rows) > 0
        for row in rows:
            assert row["label"], f"Empty label in cluster row: {dict(row)}"
            assert row["size"] > 0, f"Zero size in cluster row: {dict(row)}"
            assert row["naming_source"] in ("deterministic", "llm"), (
                f"Invalid naming_source: {row['naming_source']}"
            )

    def test_cluster_size_matches_member_count(self) -> None:
        """clusters.size equals the actual number of symbols assigned to that cluster.

        Uses min_size=1 so all singletons are retained as clusters, then verifies
        the size column matches the actual DB count for each cluster.
        """
        from seam.indexer.cluster_index import index_clusters
        from seam.indexer.db import init_db

        conn = init_db(Path(":memory:"))
        symbols = [{"name": "fn1"}, {"name": "fn2"}, {"name": "fn3"}]
        _seed_db(conn, symbols, [])  # no edges → 3 singleton communities

        # Explicitly pass min_size=1 to retain singletons for this test
        index_clusters(conn, naming_mode="deterministic", min_size=1)

        rows = conn.execute("SELECT id, size FROM clusters").fetchall()
        # Should have 3 singleton clusters (one per node)
        assert len(rows) == 3, f"Expected 3 singleton clusters, got {len(rows)}"
        for row in rows:
            actual = conn.execute(
                "SELECT COUNT(*) FROM symbols WHERE cluster_id = ?", (row["id"],)
            ).fetchone()[0]
            assert actual == row["size"], (
                f"Cluster {row['id']} size={row['size']} but has {actual} members"
            )


# ── CI2: Empty DB ─────────────────────────────────────────────────────────────


class TestClusterIndexEmptyDB:
    """CI2: index_clusters on a DB with no symbols completes without error."""

    def test_empty_db_no_error(self) -> None:
        """index_clusters on empty DB completes silently."""
        from seam.indexer.cluster_index import index_clusters
        from seam.indexer.db import init_db

        conn = init_db(Path(":memory:"))
        # No symbols or edges seeded

        try:
            index_clusters(conn, naming_mode="deterministic")
        except Exception as exc:
            pytest.fail(f"index_clusters raised on empty DB: {exc}")

        # clusters table should still be empty
        cluster_count = conn.execute("SELECT COUNT(*) FROM clusters").fetchone()[0]
        assert cluster_count == 0


# ── CI3: Graph with clear communities ─────────────────────────────────────────


class TestClusterIndexClearCommunities:
    """CI3: Two-community graph produces ≥2 clusters."""

    def test_two_communities_detected(self) -> None:
        """Two tightly connected groups → at least 2 clusters in the DB."""
        from seam.indexer.cluster_index import index_clusters
        from seam.indexer.db import init_db

        conn = init_db(Path(":memory:"))

        symbols = [
            {"name": "a1"}, {"name": "a2"}, {"name": "a3"},
            {"name": "b1"}, {"name": "b2"}, {"name": "b3"},
        ]
        edges = [
            # Community A
            {"source": "a1", "target": "a2"},
            {"source": "a2", "target": "a3"},
            {"source": "a1", "target": "a3"},
            # Community B
            {"source": "b1", "target": "b2"},
            {"source": "b2", "target": "b3"},
            {"source": "b1", "target": "b3"},
            # Bridge
            {"source": "a1", "target": "b1"},
        ]
        _seed_db(conn, symbols, edges)

        index_clusters(conn, naming_mode="deterministic")

        cluster_count = conn.execute("SELECT COUNT(*) FROM clusters").fetchone()[0]
        assert cluster_count >= 2, f"Expected ≥2 clusters, got {cluster_count}"


# ── CI4: Error resilience ─────────────────────────────────────────────────────


class TestClusterIndexErrorResilience:
    """CI4: Errors during clustering leave cluster_id NULL, don't abort."""

    def test_clustering_error_leaves_nulls(self) -> None:
        """When detect_communities raises, symbols keep NULL cluster_id."""
        from seam.indexer.cluster_index import index_clusters
        from seam.indexer.db import init_db

        conn = init_db(Path(":memory:"))
        symbols = [{"name": "fn1"}, {"name": "fn2"}]
        edges = [{"source": "fn1", "target": "fn2"}]
        _seed_db(conn, symbols, edges)

        # Stub detect_communities to raise
        with patch("seam.indexer.cluster_index.detect_communities") as mock_detect:
            mock_detect.side_effect = RuntimeError("simulated failure")
            # Must not raise
            try:
                index_clusters(conn, naming_mode="deterministic")
            except Exception as exc:
                pytest.fail(f"index_clusters should not propagate clustering errors: {exc}")

        # cluster_id should remain NULL (error path leaves them unpopulated)
        null_count = conn.execute(
            "SELECT COUNT(*) FROM symbols WHERE cluster_id IS NULL"
        ).fetchone()[0]
        assert null_count == 2, f"Expected 2 NULL cluster_ids after error, got {null_count}"

    def test_naming_error_falls_back(self) -> None:
        """When LLM naming fails, falls back to deterministic — no abort."""
        from seam.indexer.cluster_index import index_clusters
        from seam.indexer.db import init_db

        conn = init_db(Path(":memory:"))
        symbols = [{"name": "fn1"}, {"name": "fn2"}]
        edges = [{"source": "fn1", "target": "fn2"}]
        _seed_db(conn, symbols, edges)

        with patch("seam.analysis.cluster_naming._call_llm_for_label") as mock_llm:
            mock_llm.side_effect = RuntimeError("LLM down")
            # Should complete without error even with LLM failure
            try:
                index_clusters(conn, naming_mode="llm", llm_api_key="key")
            except Exception as exc:
                pytest.fail(f"LLM failure should not abort index_clusters: {exc}")

        # Clusters should still be created with deterministic fallback
        cluster_count = conn.execute("SELECT COUNT(*) FROM clusters").fetchone()[0]
        assert cluster_count > 0


# ── CI5: Deterministic DB IDs ─────────────────────────────────────────────────


class TestClusterIndexDeterministicIDs:
    """CI5: Same graph clustered twice → identical clusters.id and symbols.cluster_id."""

    def _make_connected_graph(self):
        """Three-node graph: a→b→c (all connected, should form one cluster)."""
        return (
            [{"name": "a"}, {"name": "b"}, {"name": "c"}],
            [{"source": "a", "target": "b"}, {"source": "b", "target": "c"}],
        )

    def test_cluster_ids_are_stable_across_reruns(self) -> None:
        """Clustering the same graph twice yields identical clusters.id values."""
        from seam.indexer.cluster_index import index_clusters
        from seam.indexer.db import init_db

        # First run
        conn1 = init_db(Path(":memory:"))
        symbols, edges = self._make_connected_graph()
        _seed_db(conn1, symbols, edges)
        index_clusters(conn1, naming_mode="deterministic")
        ids_run1 = {
            row["id"]: (row["label"], row["size"])
            for row in conn1.execute("SELECT id, label, size FROM clusters ORDER BY id").fetchall()
        }
        cluster_ids_run1 = [
            (row["name"], row["cluster_id"])
            for row in conn1.execute(
                "SELECT name, cluster_id FROM symbols ORDER BY name"
            ).fetchall()
        ]

        # Second run (fresh DB, same data)
        conn2 = init_db(Path(":memory:"))
        _seed_db(conn2, symbols, edges)
        index_clusters(conn2, naming_mode="deterministic")
        ids_run2 = {
            row["id"]: (row["label"], row["size"])
            for row in conn2.execute("SELECT id, label, size FROM clusters ORDER BY id").fetchall()
        }
        cluster_ids_run2 = [
            (row["name"], row["cluster_id"])
            for row in conn2.execute(
                "SELECT name, cluster_id FROM symbols ORDER BY name"
            ).fetchall()
        ]

        assert ids_run1 == ids_run2, (
            f"Cluster rows differ across runs:\n  run1={ids_run1}\n  run2={ids_run2}"
        )
        assert cluster_ids_run1 == cluster_ids_run2, (
            f"Symbol cluster_ids differ across runs:\n  run1={cluster_ids_run1}\n  run2={cluster_ids_run2}"
        )

    def test_rerun_on_same_conn_yields_stable_ids(self) -> None:
        """Running index_clusters twice on the same connection yields the same DB cluster IDs.

        This is the DELETE+re-INSERT path. IDs must not drift (not AUTOINCREMENT).
        """
        from seam.indexer.cluster_index import index_clusters
        from seam.indexer.db import init_db

        conn = init_db(Path(":memory:"))
        symbols, edges = self._make_connected_graph()
        _seed_db(conn, symbols, edges)

        index_clusters(conn, naming_mode="deterministic")
        ids_after_first = [
            row[0] for row in conn.execute("SELECT id FROM clusters ORDER BY id").fetchall()
        ]

        # Re-run on the same data
        index_clusters(conn, naming_mode="deterministic")
        ids_after_second = [
            row[0] for row in conn.execute("SELECT id FROM clusters ORDER BY id").fetchall()
        ]

        assert ids_after_first == ids_after_second, (
            f"Cluster IDs drifted on second run: {ids_after_first} → {ids_after_second}"
        )


# ── CI6: min_size enforcement ──────────────────────────────────────────────────


class TestClusterIndexMinSize:
    """CI6: Communities below min_size are not persisted; their symbols get cluster_id=NULL."""

    def test_singletons_suppressed_with_min_size_2(self) -> None:
        """A 3-node community + 5 isolated nodes → exactly 1 cluster, 5 symbols unclustered."""
        from seam.indexer.cluster_index import index_clusters
        from seam.indexer.db import init_db

        conn = init_db(Path(":memory:"))

        # One real community: x1/x2/x3 fully connected
        # Five isolated symbols: iso1..iso5 (no edges)
        symbols = [
            {"name": "x1"}, {"name": "x2"}, {"name": "x3"},
            {"name": "iso1"}, {"name": "iso2"}, {"name": "iso3"},
            {"name": "iso4"}, {"name": "iso5"},
        ]
        edges = [
            {"source": "x1", "target": "x2"},
            {"source": "x2", "target": "x3"},
            {"source": "x1", "target": "x3"},
        ]
        _seed_db(conn, symbols, edges)

        # min_size=2: singletons (size=1) are suppressed; the 3-node community survives
        n = index_clusters(conn, naming_mode="deterministic", min_size=2)

        cluster_count = conn.execute("SELECT COUNT(*) FROM clusters").fetchone()[0]
        assert cluster_count == 1, f"Expected exactly 1 cluster, got {cluster_count}"
        assert n == 1, f"index_clusters should return 1, got {n}"

        # All 5 isolated symbols must have cluster_id=NULL
        null_count = conn.execute(
            "SELECT COUNT(*) FROM symbols WHERE cluster_id IS NULL"
        ).fetchone()[0]
        assert null_count == 5, f"Expected 5 unclustered symbols, got {null_count}"

        # The 3 connected symbols must be clustered
        clustered_count = conn.execute(
            "SELECT COUNT(*) FROM symbols WHERE cluster_id IS NOT NULL"
        ).fetchone()[0]
        assert clustered_count == 3, f"Expected 3 clustered symbols, got {clustered_count}"

    def test_min_size_1_keeps_all_singletons(self) -> None:
        """min_size=1 (opt-in) keeps every node assigned to a cluster."""
        from seam.indexer.cluster_index import index_clusters
        from seam.indexer.db import init_db

        conn = init_db(Path(":memory:"))
        symbols = [{"name": "a"}, {"name": "b"}, {"name": "iso"}]
        edges = [{"source": "a", "target": "b"}]
        _seed_db(conn, symbols, edges)

        index_clusters(conn, naming_mode="deterministic", min_size=1)

        # All 3 symbols must have cluster_id (including the singleton)
        null_count = conn.execute(
            "SELECT COUNT(*) FROM symbols WHERE cluster_id IS NULL"
        ).fetchone()[0]
        assert null_count == 0, f"min_size=1 should assign every symbol; got {null_count} NULLs"


# ── CI7: size accuracy ─────────────────────────────────────────────────────────


class TestClusterSizeAccuracy:
    """CI7: clusters.size equals actual count of symbols with that cluster_id."""

    def test_size_matches_member_count_with_homonym(self) -> None:
        """Two files each defining 'helper' in the same cluster → size = 2 rows."""
        from seam.indexer.cluster_index import index_clusters
        from seam.indexer.db import init_db

        conn = init_db(Path(":memory:"))

        # Insert two different files
        conn.execute(
            "INSERT INTO files (path, language, file_hash, mtime, indexed_at)"
            " VALUES ('/file_a.py', 'python', 'hash_a', 1.0, 1.0)"
        )
        file_a = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO files (path, language, file_hash, mtime, indexed_at)"
            " VALUES ('/file_b.py', 'python', 'hash_b', 2.0, 2.0)"
        )
        file_b = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        # 'helper' defined in both files + two connected symbols (so helper gets clustered)
        conn.execute(
            "INSERT INTO symbols (file_id, name, kind, start_line, end_line)"
            " VALUES (?, 'helper', 'function', 1, 5)",
            (file_a,),
        )
        conn.execute(
            "INSERT INTO symbols (file_id, name, kind, start_line, end_line)"
            " VALUES (?, 'helper', 'function', 1, 5)",
            (file_b,),
        )
        conn.execute(
            "INSERT INTO symbols (file_id, name, kind, start_line, end_line)"
            " VALUES (?, 'driver', 'function', 6, 10)",
            (file_a,),
        )
        # Edge: driver calls helper → they're in the same community
        conn.execute(
            "INSERT INTO edges (source_name, target_name, kind, file_id, line, confidence)"
            " VALUES ('driver', 'helper', 'call', ?, 7, 'INFERRED')",
            (file_a,),
        )
        conn.commit()

        index_clusters(conn, naming_mode="deterministic", min_size=2)

        rows = conn.execute("SELECT id, size FROM clusters").fetchall()
        for row in rows:
            actual = conn.execute(
                "SELECT COUNT(*) FROM symbols WHERE cluster_id = ?", (row["id"],)
            ).fetchone()[0]
            assert actual == row["size"], (
                f"Cluster {row['id']} size={row['size']} but has {actual} member rows"
            )


# ── CI8: Orphan clearance ─────────────────────────────────────────────────────


class TestOrphanClearance:
    """CI8: Clustering an empty symbol set after a populated run leaves no ghost clusters."""

    def test_empty_recluster_clears_previous_clusters(self) -> None:
        """After a successful cluster run, re-clustering with no symbols leaves clusters table empty."""
        from seam.indexer.cluster_index import index_clusters
        from seam.indexer.db import init_db

        conn = init_db(Path(":memory:"))
        symbols = [{"name": "a"}, {"name": "b"}]
        edges = [{"source": "a", "target": "b"}]
        _seed_db(conn, symbols, edges)

        # First run: populate clusters
        n1 = index_clusters(conn, naming_mode="deterministic", min_size=1)
        assert n1 > 0, "Expected clusters from first run"

        # Now delete all symbols + files to simulate an empty re-index
        conn.execute("DELETE FROM edges")
        conn.execute("DELETE FROM symbols")
        conn.execute("DELETE FROM files")
        conn.commit()

        # Second run on empty symbol set: must clear old clusters, return 0
        n2 = index_clusters(conn, naming_mode="deterministic", min_size=1)
        assert n2 == 0, f"Expected 0 clusters on empty re-index, got {n2}"

        cluster_count = conn.execute("SELECT COUNT(*) FROM clusters").fetchone()[0]
        assert cluster_count == 0, f"Expected clusters table empty, got {cluster_count} rows"

        # All symbols already deleted; no symbol should have a non-NULL cluster_id
        # (this verifies the UPDATE symbols SET cluster_id=NULL ran without error on empty table)
        null_orphans = conn.execute(
            "SELECT COUNT(*) FROM symbols WHERE cluster_id IS NOT NULL"
        ).fetchone()[0]
        assert null_orphans == 0, f"Unexpected non-NULL cluster_ids: {null_orphans}"


# ── CI9: Failure signal ───────────────────────────────────────────────────────


class TestClusterIndexFailureSignal:
    """CI9: index_clusters returns -1 on error, not 0."""

    def test_returns_minus_one_on_detect_communities_error(self) -> None:
        """When detect_communities raises, index_clusters returns -1 (not 0)."""
        from seam.indexer.cluster_index import index_clusters
        from seam.indexer.db import init_db

        conn = init_db(Path(":memory:"))
        _seed_db(conn, [{"name": "fn1"}, {"name": "fn2"}], [{"source": "fn1", "target": "fn2"}])

        with patch("seam.indexer.cluster_index.detect_communities") as mock_detect:
            mock_detect.side_effect = RuntimeError("simulated failure")
            result = index_clusters(conn, naming_mode="deterministic")

        assert result == -1, f"Expected -1 on error, got {result}"

    def test_zero_is_reserved_for_genuine_empty_graph(self) -> None:
        """An empty DB (no symbols) returns 0, not -1."""
        from seam.indexer.cluster_index import index_clusters
        from seam.indexer.db import init_db

        conn = init_db(Path(":memory:"))
        # No symbols seeded
        result = index_clusters(conn, naming_mode="deterministic")
        assert result == 0, f"Empty graph should return 0, got {result}"


# ── CI10: Synthesized edges must NOT pollute clustering (regression) ───────────


class TestSynthesizedEdgesExcludedFromClustering:
    """CI10: index_clusters ignores synthesized edges (synthesized_by IS NOT NULL).

    Regression guard for the feedback bug: the edge-synthesis post-pass runs AFTER
    clustering and its over-approximated edges persist across runs. If clustering
    consumed them, two unrelated modules densely bridged by synthesized dispatch
    edges would collapse into one community on every re-cluster.
    """

    def test_synthesized_bridge_does_not_merge_communities(self) -> None:
        from seam.indexer.cluster_index import index_clusters
        from seam.indexer.db import init_db

        conn = init_db(Path(":memory:"))

        # Two disjoint triangles (real edges only): a-side and b-side.
        symbols = [
            {"name": "a1"}, {"name": "a2"}, {"name": "a3"},
            {"name": "b1"}, {"name": "b2"}, {"name": "b3"},
        ]
        real_edges = [
            {"source": "a1", "target": "a2"}, {"source": "a2", "target": "a3"},
            {"source": "a3", "target": "a1"},
            {"source": "b1", "target": "b2"}, {"source": "b2", "target": "b3"},
            {"source": "b3", "target": "b1"},
        ]
        _seed_db(conn, symbols, real_edges)

        # Densely bridge the two triangles with SYNTHESIZED edges. If these were
        # fed to Louvain they would merge a-side and b-side into one community.
        file_id = conn.execute("SELECT id FROM files LIMIT 1").fetchone()[0]
        for a in ("a1", "a2", "a3"):
            for b in ("b1", "b2", "b3"):
                conn.execute(
                    "INSERT INTO edges (source_name, target_name, kind, file_id, line,"
                    " confidence, synthesized_by) VALUES (?, ?, 'call', ?, 0, 'INFERRED',"
                    " 'interface-override')",
                    (a, b, file_id),
                )
        conn.commit()

        index_clusters(conn, naming_mode="deterministic", min_size=1)

        a1_cluster = conn.execute(
            "SELECT cluster_id FROM symbols WHERE name = 'a1'"
        ).fetchone()[0]
        b1_cluster = conn.execute(
            "SELECT cluster_id FROM symbols WHERE name = 'b1'"
        ).fetchone()[0]

        assert a1_cluster is not None and b1_cluster is not None
        assert a1_cluster != b1_cluster, (
            "Synthesized edges leaked into clustering — a-side and b-side were merged "
            "by the synthetic bridge (clustering must filter synthesized_by IS NOT NULL)"
        )
