"""Integration tests for handle_seam_clusters + seam_context cluster enrichment.

Tests the handler layer with a seeded SQLite DB.

Test groups:
    H1 — handle_seam_clusters with no id: returns cluster summary list
    H2 — handle_seam_clusters with id: returns relativized member list
    H3 — handle_seam_context: includes cluster_id, cluster_label, cluster_peers
    H4 — seam init on fixtures: produces ≥1 cluster; seam status shows cluster count
    H5 — Empty clusters: handle_seam_clusters returns []
"""

import sqlite3
from pathlib import Path

from seam.indexer.cluster_index import index_clusters
from seam.indexer.db import init_db
from seam.server.tools import handle_seam_clusters, handle_seam_context

# ── Helpers ───────────────────────────────────────────────────────────────────

def _seed_clustered_db(tmp_path: Path) -> tuple[sqlite3.Connection, Path, dict]:
    """Seed a DB with two clusters and return (conn, project_root, info)."""
    db_path = tmp_path / ".seam" / "seam.db"
    db_path.parent.mkdir()

    # Create a dummy source file in the project root (needed for path resolution)
    src_file = tmp_path / "src.py"
    src_file.write_text("x = 1\n")

    conn = init_db(db_path)

    # Insert files + symbols + edges
    conn.execute(
        "INSERT INTO files (path, language, file_hash, mtime, indexed_at)"
        " VALUES (?, 'python', 'abc', 1.0, 1.0)",
        (str(src_file),),
    )
    file_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    for name in ["alpha", "beta", "gamma", "delta", "epsilon"]:
        conn.execute(
            "INSERT INTO symbols (file_id, name, kind, start_line, end_line)"
            " VALUES (?, ?, 'function', 1, 5)",
            (file_id, name),
        )
    # Edges: alpha/beta/gamma tightly connected, delta/epsilon separate
    for src, tgt in [("alpha", "beta"), ("beta", "gamma"), ("alpha", "gamma"),
                      ("delta", "epsilon"), ("alpha", "delta")]:
        conn.execute(
            "INSERT INTO edges (source_name, target_name, kind, file_id, line, confidence)"
            " VALUES (?, ?, 'call', ?, 1, 'INFERRED')",
            (src, tgt, file_id),
        )
    conn.commit()

    # Run clustering so the DB has actual cluster assignments
    index_clusters(conn, naming_mode="deterministic")

    cluster_ids = [row[0] for row in conn.execute("SELECT id FROM clusters ORDER BY id").fetchall()]

    return conn, tmp_path, {"cluster_ids": cluster_ids, "src_file": src_file}


# ── H1: list all clusters ─────────────────────────────────────────────────────


class TestHandleSeamClustersListAll:
    """H1: Without cluster_id, handler returns cluster summary list."""

    def test_returns_list(self, tmp_path: Path) -> None:
        """handle_seam_clusters(no id) → list of dicts."""
        conn, root, info = _seed_clustered_db(tmp_path)
        result = handle_seam_clusters(conn, root)
        assert isinstance(result, list)

    def test_clusters_have_required_fields(self, tmp_path: Path) -> None:
        """Each cluster in list has id, label, size."""
        conn, root, info = _seed_clustered_db(tmp_path)
        result = handle_seam_clusters(conn, root)
        assert len(result) > 0
        for c in result:
            assert "id" in c
            assert "label" in c
            assert "size" in c

    def test_returns_expected_cluster_count(self, tmp_path: Path) -> None:
        """The number of clusters matches what was computed."""
        conn, root, info = _seed_clustered_db(tmp_path)
        expected = conn.execute("SELECT COUNT(*) FROM clusters").fetchone()[0]
        result = handle_seam_clusters(conn, root)
        assert len(result) == expected


# ── H2: members of a cluster + path relativization ───────────────────────────


class TestHandleSeamClustersMembersRelativized:
    """H2: With cluster_id, handler returns member list with relativized paths."""

    def test_members_returned_for_valid_id(self, tmp_path: Path) -> None:
        """handle_seam_clusters(id=N) returns member list."""
        conn, root, info = _seed_clustered_db(tmp_path)
        cid = info["cluster_ids"][0]
        result = handle_seam_clusters(conn, root, cluster_id=cid)
        assert isinstance(result, list)
        assert len(result) > 0

    def test_member_file_paths_relativized(self, tmp_path: Path) -> None:
        """File paths in member rows are relative to project root (not absolute)."""
        conn, root, info = _seed_clustered_db(tmp_path)
        cid = info["cluster_ids"][0]
        result = handle_seam_clusters(conn, root, cluster_id=cid)
        for m in result:
            # Path must NOT be absolute (it should be relativized to root)
            assert not Path(m["file"]).is_absolute(), (
                f"Expected relative path, got absolute: {m['file']}"
            )

    def test_member_row_has_required_fields(self, tmp_path: Path) -> None:
        """Member rows have name, file, line, kind."""
        conn, root, info = _seed_clustered_db(tmp_path)
        cid = info["cluster_ids"][0]
        result = handle_seam_clusters(conn, root, cluster_id=cid)
        for m in result:
            assert "name" in m
            assert "file" in m
            assert "line" in m
            assert "kind" in m

    def test_unknown_cluster_id_returns_empty(self, tmp_path: Path) -> None:
        """Unknown cluster_id → empty list (not an error)."""
        conn, root, info = _seed_clustered_db(tmp_path)
        result = handle_seam_clusters(conn, root, cluster_id=9999)
        assert result == []


# ── H3: seam_context enriched with cluster fields ────────────────────────────


class TestHandleSeamContextClusterFields:
    """H3: handle_seam_context includes cluster_id, cluster_label, cluster_peers."""

    def test_context_includes_cluster_id(self, tmp_path: Path) -> None:
        """context result has cluster_id field."""
        conn, root, info = _seed_clustered_db(tmp_path)
        result = handle_seam_context(conn, "alpha", root)
        assert result is not None
        assert "cluster_id" in result

    def test_context_cluster_id_is_set(self, tmp_path: Path) -> None:
        """cluster_id is non-None after clustering runs."""
        conn, root, info = _seed_clustered_db(tmp_path)
        result = handle_seam_context(conn, "alpha", root)
        assert result is not None
        assert result["cluster_id"] is not None, "cluster_id should be set after clustering"

    def test_context_includes_cluster_label(self, tmp_path: Path) -> None:
        """context result has cluster_label field that is non-empty."""
        conn, root, info = _seed_clustered_db(tmp_path)
        result = handle_seam_context(conn, "alpha", root)
        assert result is not None
        assert "cluster_label" in result
        assert result["cluster_label"], "cluster_label should be non-empty"

    def test_context_includes_cluster_peers(self, tmp_path: Path) -> None:
        """context result has cluster_peers field (list, may be empty for singleton)."""
        conn, root, info = _seed_clustered_db(tmp_path)
        result = handle_seam_context(conn, "alpha", root)
        assert result is not None
        assert "cluster_peers" in result
        assert isinstance(result["cluster_peers"], list)

    def test_context_cluster_id_none_when_unassigned(self, tmp_path: Path) -> None:
        """When no clustering has been run, cluster_id is None."""
        from seam.indexer.db import init_db as fresh_init_db

        db_path = tmp_path / ".seam" / "no_clusters.db"
        db_path.parent.mkdir(exist_ok=True)

        src = tmp_path / "no_cluster_src.py"
        src.write_text("x=1\n")

        conn = fresh_init_db(db_path)
        conn.execute(
            "INSERT INTO files (path, language, file_hash, mtime, indexed_at)"
            " VALUES (?, 'python', 'h', 1.0, 1.0)",
            (str(src),),
        )
        fid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO symbols (file_id, name, kind, start_line, end_line)"
            " VALUES (?, 'lonely_fn', 'function', 1, 5)",
            (fid,),
        )
        conn.commit()
        # No index_clusters called — cluster_id stays NULL

        result = handle_seam_context(conn, "lonely_fn", tmp_path)
        assert result is not None
        assert result["cluster_id"] is None
        assert result["cluster_label"] is None
        assert result["cluster_peers"] == []


# ── H4: seam init on fixtures → ≥1 cluster ───────────────────────────────────


class TestSeamInitProducesClusters:
    """H4: Running seam init on the test fixtures produces ≥1 cluster."""

    def test_init_on_fixtures_produces_clusters(self, tmp_path: Path) -> None:
        """After init_db + index_one_file on fixtures + index_clusters → ≥1 cluster."""
        from seam.indexer.pipeline import index_one_file, walk_project

        fixtures = Path(__file__).parent.parent / "fixtures"
        db_path = tmp_path / ".seam" / "seam.db"
        db_path.parent.mkdir()

        conn = init_db(db_path)

        files = walk_project(fixtures)
        for f in files:
            index_one_file(conn, f)

        n_clusters = index_clusters(conn, naming_mode="deterministic")

        assert n_clusters >= 1, (
            f"Expected ≥1 cluster from fixtures, got {n_clusters}"
        )

        # Verify DB rows were written
        db_count = conn.execute("SELECT COUNT(*) FROM clusters").fetchone()[0]
        assert db_count == n_clusters

        conn.close()


# ── H5: Empty clusters ────────────────────────────────────────────────────────


class TestHandleSeamClustersEmpty:
    """H5: When no clusters are computed, handler returns empty list."""

    def test_empty_clusters_returns_list(self, tmp_path: Path) -> None:
        """No clusters in DB → handle_seam_clusters returns [] not an error."""
        db_path = tmp_path / ".seam" / "seam.db"
        db_path.parent.mkdir()
        conn = init_db(db_path)

        result = handle_seam_clusters(conn, tmp_path)
        assert result == []
