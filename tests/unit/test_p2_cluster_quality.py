"""P2 — cluster quality tests (confidence-filtered Louvain, two-level labels, cohesion).

TDD: written before implementation. Covers the three P2 sub-changes plus the
v7→v8 clusters.cohesion migration.

Test groups:
    L  — Two-level cluster labels: module dir over leaf, GENERIC_DIRS skipped.
    F  — Confidence filter reduces edges passed to Louvain on large graphs only.
    C  — Cohesion stored in clusters.cohesion + used as a +0.1 search rescore bonus
         only when present (NULL/absent → byte-identical ranking).
    MIG — v7→v8 migration: idempotent, fresh DB has cohesion, old (v7) DB gains it.
"""

import sqlite3
import tempfile
from pathlib import Path

from seam.analysis.cluster_naming import GENERIC_DIRS, _dominant_dir, deterministic_label

# ── L: Two-level cluster labels ──────────────────────────────────────────────


class TestTwoLevelLabels:
    """L: _dominant_dir walks up to the highest non-generic module dir."""

    def test_picks_module_dir_over_leaf(self) -> None:
        """Files under query/fts.py + query/engine.py → 'query' (module), not 'fts'/leaf.

        The leaf parent dir is already 'query' here, but for a nested file like
        'seam/query/sub/x.py' the immediate parent ('sub') should yield to the
        higher module dir ('query') only when the lower dir IS generic — see the
        generic-skip test. This case confirms the dominant module dir is returned.
        """
        members = [
            {"name": "build_match_query", "file": "seam/query/fts.py", "degree": 3},
            {"name": "search", "file": "seam/query/engine.py", "degree": 5},
        ]
        assert _dominant_dir(members) == "query"

    def test_generic_dirs_skipped_to_reach_module(self) -> None:
        """A file directly under a generic dir (src/render.py) → walk up is impossible,
        but a file at 'app/render/widget.py' must yield 'render', NOT the generic 'app'.

        The immediate parent of widget.py is 'render' (non-generic) → returned as-is.
        The key generic-skip case is when the immediate parent IS generic.
        """
        members = [
            {"name": "draw", "file": "app/render/widget.py", "degree": 2},
            {"name": "paint", "file": "app/render/canvas.py", "degree": 2},
        ]
        assert _dominant_dir(members) == "render"

    def test_immediate_generic_parent_walks_up(self) -> None:
        """When the immediate parent dir is generic, walk UP to the first non-generic.

        'render/src/x.py' → immediate parent 'src' is generic → walk up to 'render'.
        """
        members = [
            {"name": "draw", "file": "render/src/x.py", "degree": 2},
            {"name": "paint", "file": "render/src/y.py", "degree": 2},
        ]
        assert _dominant_dir(members) == "render"

    def test_all_generic_returns_topmost_nongeneric_or_none(self) -> None:
        """When every dir on the path is generic, _dominant_dir returns None.

        'src/lib/x.py' → both 'lib' and 'src' generic → None (no module dir).
        """
        members = [
            {"name": "f", "file": "src/lib/x.py", "degree": 1},
            {"name": "g", "file": "src/lib/y.py", "degree": 1},
        ]
        assert _dominant_dir(members) is None

    def test_generic_dirs_constant_contents(self) -> None:
        """GENERIC_DIRS holds the documented module-noise dir names."""
        assert {"src", "lib", "app", "pkg", "main", "core", "base"} <= GENERIC_DIRS

    def test_label_uses_module_dir(self) -> None:
        """deterministic_label combines the module dir with the anchor symbol."""
        members = [
            {"name": "draw", "file": "render/src/x.py", "degree": 9},
            {"name": "paint", "file": "render/src/y.py", "degree": 2},
        ]
        label = deterministic_label(members)
        assert label.startswith("render"), label
        assert "draw" in label


# ── F: Confidence-filtered Louvain ───────────────────────────────────────────


class TestConfidenceFilter:
    """F: detect_communities accepts an optional confidence filter; cluster_index
    only applies it on large graphs (symbol_count > threshold)."""

    def test_detect_communities_accepts_no_filter_by_default(self) -> None:
        """Default call signature unchanged — no filter argument required."""
        from seam.analysis.clustering import detect_communities

        result = detect_communities(["a", "b"], [("a", "b")])
        assert result["a"] == result["b"]

    def test_small_graph_keeps_all_edges(self) -> None:
        """Below the threshold, cluster_index passes ALL edges (no filter)."""
        from seam.indexer.db import init_db

        conn = init_db(Path(":memory:"))
        _seed_two_module_graph(conn, noisy=True)
        from seam.indexer.cluster_index import index_clusters

        n = index_clusters(conn, min_size=1)
        conn.close()
        assert n >= 1

    def test_large_graph_filters_low_confidence_edges(self, monkeypatch) -> None:
        """Above the threshold AND filter on, only EXTRACTED + import-INFERRED edges
        are passed to detect_communities — noisy AMBIGUOUS call edges are dropped,
        so the two modules do NOT merge into one cluster.

        We verify by spying on the edges that reach detect_communities.
        """
        import seam.indexer.cluster_index as ci

        captured: dict[str, list] = {}

        real_detect = ci.detect_communities

        def _spy(nodes, edges):
            captured["edges"] = list(edges)
            return real_detect(nodes, edges)

        monkeypatch.setattr(ci, "detect_communities", _spy)
        # Force the filter ON regardless of repo size by setting the threshold to 0.
        monkeypatch.setattr(ci.config, "SEAM_CLUSTER_CONFIDENCE_FILTER", "0")

        from seam.indexer.db import init_db

        conn = init_db(Path(":memory:"))
        _seed_two_module_graph(conn, noisy=True)
        ci.index_clusters(conn, min_size=1)
        conn.close()

        # The single AMBIGUOUS cross-module call edge must NOT be in the passed set.
        passed = set(captured["edges"])
        assert ("alpha_main", "beta_helper") not in passed, (
            "AMBIGUOUS noise edge should be filtered out on large graphs"
        )

    def test_filter_off_passes_all_edges(self, monkeypatch) -> None:
        """SEAM_CLUSTER_CONFIDENCE_FILTER='off' disables the filter entirely."""
        import seam.indexer.cluster_index as ci

        captured: dict[str, list] = {}
        real_detect = ci.detect_communities

        def _spy(nodes, edges):
            captured["edges"] = list(edges)
            return real_detect(nodes, edges)

        monkeypatch.setattr(ci, "detect_communities", _spy)
        monkeypatch.setattr(ci.config, "SEAM_CLUSTER_CONFIDENCE_FILTER", "off")

        from seam.indexer.db import init_db

        conn = init_db(Path(":memory:"))
        _seed_two_module_graph(conn, noisy=True)
        ci.index_clusters(conn, min_size=1)
        conn.close()

        passed = set(captured["edges"])
        assert ("alpha_main", "beta_helper") in passed, (
            "filter=off must pass every edge, including AMBIGUOUS"
        )


# ── C: Cohesion score ─────────────────────────────────────────────────────────


class TestCohesion:
    """C: cohesion stored per cluster + used as a small additive search bonus."""

    def test_cohesion_column_populated_after_index(self) -> None:
        """index_clusters writes a non-NULL cohesion ratio in [0, 1] per cluster."""
        from seam.indexer.cluster_index import index_clusters
        from seam.indexer.db import init_db

        conn = init_db(Path(":memory:"))
        _seed_two_module_graph(conn, noisy=False)
        index_clusters(conn, min_size=1)
        rows = conn.execute("SELECT cohesion FROM clusters").fetchall()
        conn.close()
        assert rows, "expected at least one cluster"
        for r in rows:
            assert r["cohesion"] is not None
            assert 0.0 <= float(r["cohesion"]) <= 1.0

    def test_rescore_applies_cohesion_bonus_when_present(self) -> None:
        """Two otherwise-identical rows in the SAME cluster: the one with
        cohesion=1.0 outranks the one with cohesion=None by exactly +0.1.

        Same cluster_id → the cluster-peer bonus applies equally to both, so the
        ONLY difference between their scores is the cohesion term we isolate here.
        """
        from seam.query.fts import rescore

        high = {"symbol": "aaa", "file": "x.py", "score": 1.0,
                "cluster_id": 1, "cohesion": 1.0}
        nul = {"symbol": "bbb", "file": "x.py", "score": 1.0,
               "cluster_id": 1, "cohesion": None}
        out = rescore([high, nul], terms=[])
        by_symbol = {r["symbol"]: r["score"] for r in out}
        assert by_symbol["aaa"] > by_symbol["bbb"], "cohesion present must add a bonus"
        # The bonus is small (+0.1 * cohesion); same cluster so cluster-peer cancels.
        assert abs((by_symbol["aaa"] - by_symbol["bbb"]) - 0.1) < 1e-9

    def test_rescore_unchanged_when_cohesion_absent(self) -> None:
        """When NO row carries a cohesion key, the cohesion term never fires.

        Both rows share a cluster_id (so the cluster-peer bonus applies equally)
        and carry no cohesion key. With no terms, the only bonus either row can get
        is the cluster-peer +20. We assert each final score is exactly base+20 —
        i.e. NO fractional cohesion term leaked in.
        """
        from seam.query.fts import rescore

        rows = [
            {"symbol": "a", "file": "x.py", "score": 2.0, "cluster_id": 1},
            {"symbol": "b", "file": "y.py", "score": 1.0, "cluster_id": 1},
        ]
        out = rescore(rows, terms=[])
        after = {r["symbol"]: r["score"] for r in out}
        # Cluster-peer (+20) is the only applicable bonus; scores must be integral
        # (no +0.1*cohesion fraction). 2.0+20 and 1.0+20.
        assert after["a"] == 22.0
        assert after["b"] == 21.0


# ── MIG: v7 → v8 migration (clusters.cohesion) ───────────────────────────────


def _make_v7_db(db_path: Path) -> None:
    """Build a minimal v7 DB whose clusters table has NO cohesion column."""
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        PRAGMA foreign_keys = ON;
        CREATE TABLE files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            path TEXT NOT NULL UNIQUE, language TEXT NOT NULL,
            file_hash TEXT NOT NULL, mtime REAL NOT NULL, indexed_at REAL NOT NULL
        );
        CREATE TABLE symbols (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
            name TEXT NOT NULL, kind TEXT NOT NULL,
            start_line INTEGER NOT NULL, end_line INTEGER NOT NULL,
            docstring TEXT, cluster_id INTEGER, signature TEXT, decorators TEXT,
            is_exported INTEGER, visibility TEXT, qualified_name TEXT
        );
        CREATE TABLE edges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_name TEXT NOT NULL, target_name TEXT NOT NULL, kind TEXT NOT NULL,
            file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
            line INTEGER NOT NULL, confidence TEXT NOT NULL DEFAULT 'INFERRED'
        );
        CREATE VIRTUAL TABLE symbols_fts USING fts5(
            name, docstring, signature, content='symbols', content_rowid='id'
        );
        CREATE TABLE comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
            line INTEGER NOT NULL, marker TEXT NOT NULL, text TEXT NOT NULL
        );
        CREATE TABLE clusters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            label TEXT NOT NULL, size INTEGER NOT NULL, naming_source TEXT NOT NULL
        );
        CREATE TABLE import_mappings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
            local_name TEXT NOT NULL, exported_name TEXT NOT NULL,
            source_module TEXT NOT NULL, is_default INTEGER NOT NULL DEFAULT 0,
            is_namespace INTEGER NOT NULL DEFAULT 0, is_wildcard INTEGER NOT NULL DEFAULT 0,
            line INTEGER NOT NULL
        );
        CREATE TABLE embeddings (
            symbol_id INTEGER PRIMARY KEY REFERENCES symbols(id) ON DELETE CASCADE,
            model TEXT NOT NULL, dim INTEGER NOT NULL, vector BLOB NOT NULL
        );
        CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT OR IGNORE INTO metadata(key, value) VALUES ('schema_version', '7');
        INSERT OR IGNORE INTO metadata(key, value) VALUES ('seam_version', '0.2.0');
    """)
    conn.close()


def _clusters_cols(conn: sqlite3.Connection) -> set[str]:
    return {r["name"] for r in conn.execute("PRAGMA table_info(clusters)").fetchall()}


class TestMigrationV7ToV8:
    def test_fresh_db_has_cohesion_column(self) -> None:
        from seam.indexer.db import init_db

        conn = init_db(Path(":memory:"))
        cols = _clusters_cols(conn)
        ver = conn.execute(
            "SELECT value FROM metadata WHERE key='schema_version'"
        ).fetchone()[0]
        conn.close()
        assert "cohesion" in cols
        assert int(ver) >= 8

    def test_v7_db_gains_cohesion_column(self) -> None:
        from seam.indexer.db import init_db

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = Path(f.name)
        try:
            _make_v7_db(db_path)
            conn = init_db(db_path)
            cols = _clusters_cols(conn)
            ver = conn.execute(
                "SELECT value FROM metadata WHERE key='schema_version'"
            ).fetchone()[0]
            conn.close()
            assert "cohesion" in cols
            # v7→v8 adds cohesion; later migrations bump the version further, so the
            # stored version is the current schema version (>= 8), not exactly 8.
            assert int(ver) >= 8
        finally:
            db_path.unlink(missing_ok=True)

    def test_migration_idempotent(self) -> None:
        from seam.indexer.db import init_db

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = Path(f.name)
        try:
            _make_v7_db(db_path)
            c1 = init_db(db_path)
            v1 = c1.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()[0]
            c1.close()
            # Second open via connect() must not error or change version.
            from seam.indexer.db import connect

            c2 = connect(db_path)
            v2 = c2.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()[0]
            cols = _clusters_cols(c2)
            c2.close()
            # Idempotent: a second open does not change the stored version. The value
            # is the current schema version (>= 8 after later migrations), not exactly 8.
            assert v1 == v2
            assert int(v1) >= 8
            assert "cohesion" in cols
        finally:
            db_path.unlink(missing_ok=True)


# ── Shared fixture helper ─────────────────────────────────────────────────────


def _seed_two_module_graph(conn: sqlite3.Connection, *, noisy: bool) -> None:
    """Seed two clearly-separate modules connected (optionally) by ONE noisy edge.

    Module alpha: alpha_main → alpha_a → alpha_b (EXTRACTED intra-module calls).
    Module beta:  beta_main  → beta_helper       (EXTRACTED intra-module call).
    When noisy=True, add ONE AMBIGUOUS cross-module call alpha_main → beta_helper
    that should be DROPPED by the confidence filter on large graphs.
    """
    conn.execute(
        "INSERT INTO files (path, language, file_hash, mtime, indexed_at)"
        " VALUES ('alpha.py','python','h',1.0,1.0)"
    )
    conn.execute(
        "INSERT INTO files (path, language, file_hash, mtime, indexed_at)"
        " VALUES ('beta.py','python','h',1.0,1.0)"
    )
    alpha_id = conn.execute("SELECT id FROM files WHERE path='alpha.py'").fetchone()["id"]
    beta_id = conn.execute("SELECT id FROM files WHERE path='beta.py'").fetchone()["id"]

    for name in ("alpha_main", "alpha_a", "alpha_b"):
        conn.execute(
            "INSERT INTO symbols (file_id, name, kind, start_line, end_line)"
            " VALUES (?,?,?,1,2)",
            (alpha_id, name, "function"),
        )
    for name in ("beta_main", "beta_helper"):
        conn.execute(
            "INSERT INTO symbols (file_id, name, kind, start_line, end_line)"
            " VALUES (?,?,?,1,2)",
            (beta_id, name, "function"),
        )

    def edge(src: str, tgt: str, fid: int, conf: str, kind: str = "call") -> None:
        conn.execute(
            "INSERT INTO edges (source_name, target_name, kind, file_id, line, confidence)"
            " VALUES (?,?,?,?,1,?)",
            (src, tgt, kind, fid, conf),
        )

    edge("alpha_main", "alpha_a", alpha_id, "EXTRACTED")
    edge("alpha_a", "alpha_b", alpha_id, "EXTRACTED")
    edge("beta_main", "beta_helper", beta_id, "EXTRACTED")
    if noisy:
        edge("alpha_main", "beta_helper", alpha_id, "AMBIGUOUS")
    conn.commit()
