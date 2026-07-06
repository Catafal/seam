"""Integration tests for T6 — hybrid semantic wiring in seam/query/engine.py.

TDD: Tests written BEFORE wiring (RED phase for T6 engine changes).

All tests are GATE-SAFE: fully offline, no network, no model download.
Synthetic float32 vectors are injected into the DB via struct.pack.
Embedder (embed_query) is monkeypatched at seam.query.semantic module level.

Test groups:
    H1 — SEAM_SEMANTIC=off: engine.search() / engine.query() are byte-identical to today.
    H2 — SEAM_SEMANTIC=on + no embeddings: still falls through to FTS5-only path.
    H3 — SEAM_SEMANTIC=on + synthetic embeddings: semantic-only symbol surfaces in results.
    H4 — hybrid merge: a keyword hit is never dropped (semantic ADDS recall).
    H5 — model mismatch with on: falls back to FTS5-only path.
"""

import sqlite3
import struct
from pathlib import Path
from unittest.mock import patch

from seam.indexer.db import init_db

# ── Helpers ───────────────────────────────────────────────────────────────────


def _f32(values: list[float]) -> bytes:
    """Pack a list of floats as float32 bytes."""
    return struct.pack(f"{len(values)}f", *values)


def _insert_symbol(
    conn: sqlite3.Connection,
    name: str,
    docstring: str = "",
    file_path: str = "/proj/a.py",
    kind: str = "function",
) -> int:
    """Insert a symbol and return its id."""
    conn.execute(
        "INSERT OR IGNORE INTO files (path, language, file_hash, mtime, indexed_at)"
        " VALUES (?, 'python', 'abc', 1.0, 1.0)",
        (file_path,),
    )
    file_id = conn.execute("SELECT id FROM files WHERE path = ?", (file_path,)).fetchone()["id"]

    conn.execute(
        "INSERT INTO symbols (file_id, name, kind, start_line, end_line, docstring)"
        " VALUES (?, ?, ?, 1, 5, ?)",
        (file_id, name, kind, docstring or None),
    )
    # Rebuild FTS so FTS queries work
    conn.execute("INSERT INTO symbols_fts(symbols_fts) VALUES('rebuild')")
    conn.commit()

    return conn.execute(
        "SELECT id FROM symbols WHERE name = ? AND file_id = ?", (name, file_id)
    ).fetchone()["id"]


def _insert_embedding(
    conn: sqlite3.Connection,
    symbol_id: int,
    vector: bytes,
    model: str = "test-model",
) -> None:
    """Insert a synthetic embedding for a symbol."""
    dim = len(vector) // 4
    conn.execute(
        "INSERT OR REPLACE INTO embeddings (symbol_id, model, dim, vector) VALUES (?, ?, ?, ?)",
        (symbol_id, model, dim, vector),
    )
    conn.commit()


# ── H1: SEAM_SEMANTIC=off is byte-identical to existing FTS path ──────────────


class TestSemanticOff:
    """H1 — With SEAM_SEMANTIC=off, engine.search() and engine.query() are unchanged."""

    def test_search_returns_results_when_semantic_off(self, tmp_path: Path) -> None:
        """search() with SEAM_SEMANTIC=off finds symbols the same way as before."""
        from seam.query.engine import search

        conn = init_db(tmp_path / "test.db")
        _insert_symbol(conn, "parse_input", "Parse the input string.")

        with patch("seam.config.SEAM_SEMANTIC", "off"):
            results = search(conn, "parse_input")

        conn.close()
        assert any(r["symbol"] == "parse_input" for r in results)

    def test_search_does_not_call_embed_when_off(self, tmp_path: Path) -> None:
        """When SEAM_SEMANTIC=off, embed_query is never called."""
        from seam.query.engine import search

        conn = init_db(tmp_path / "test.db")
        _insert_symbol(conn, "foo_bar")

        call_count = 0

        def mock_embed_query(text: str, model: str) -> bytes:
            nonlocal call_count
            call_count += 1
            return b""

        with patch("seam.config.SEAM_SEMANTIC", "off"):
            with patch("seam.query.semantic.embed_query", mock_embed_query):
                with patch("seam.query.semantic.is_available", return_value=True):
                    search(conn, "foo_bar")

        conn.close()
        assert call_count == 0

    def test_query_returns_results_when_semantic_off(self, tmp_path: Path) -> None:
        """query() with SEAM_SEMANTIC=off finds symbols via FTS5."""
        from seam.query.engine import query

        conn = init_db(tmp_path / "test.db")
        _insert_symbol(conn, "validate_user", "Validates a user object.")

        with patch("seam.config.SEAM_SEMANTIC", "off"):
            results = query(conn, "validate_user")

        conn.close()
        assert any(r["symbol"] == "validate_user" for r in results)

    def test_query_does_not_call_embed_when_off(self, tmp_path: Path) -> None:
        """When SEAM_SEMANTIC=off, embed_query is never called in query()."""
        from seam.query.engine import query

        conn = init_db(tmp_path / "test.db")
        _insert_symbol(conn, "make_request")

        call_count = 0

        def mock_embed_query(text: str, model: str) -> bytes:
            nonlocal call_count
            call_count += 1
            return b""

        with patch("seam.config.SEAM_SEMANTIC", "off"):
            with patch("seam.query.semantic.embed_query", mock_embed_query):
                with patch("seam.query.semantic.is_available", return_value=True):
                    query(conn, "make_request")

        conn.close()
        assert call_count == 0


# ── H2: SEAM_SEMANTIC=on + no embeddings ─────────────────────────────────────


class TestSemanticOnNoEmbeddings:
    """H2 — With SEAM_SEMANTIC=on but no embeddings in DB, FTS path is used."""

    def test_search_still_works_with_no_embeddings(self, tmp_path: Path) -> None:
        """search() falls back to FTS5 gracefully when no embeddings exist."""
        from seam.query.engine import search

        conn = init_db(tmp_path / "test.db")
        _insert_symbol(conn, "handle_request", "Handle an HTTP request.")

        query_blob = _f32([1.0, 0.0])

        with patch("seam.config.SEAM_SEMANTIC", "on"):
            with patch("seam.query.semantic.is_available", return_value=True):
                with patch("seam.query.semantic.embed_query", return_value=query_blob):
                    results = search(conn, "handle_request")

        conn.close()
        assert any(r["symbol"] == "handle_request" for r in results)

    def test_query_still_works_with_no_embeddings(self, tmp_path: Path) -> None:
        """query() falls back to FTS5 gracefully when no embeddings exist."""
        from seam.query.engine import query

        conn = init_db(tmp_path / "test.db")
        _insert_symbol(conn, "compute_hash", "Compute a SHA-256 hash.")

        query_blob = _f32([1.0, 0.0])

        with patch("seam.config.SEAM_SEMANTIC", "on"):
            with patch("seam.query.semantic.is_available", return_value=True):
                with patch("seam.query.semantic.embed_query", return_value=query_blob):
                    results = query(conn, "compute_hash")

        conn.close()
        assert any(r["symbol"] == "compute_hash" for r in results)


# ── H3: SEAM_SEMANTIC=on + synthetic embeddings → semantic symbol surfaces ────


class TestSemanticOnWithEmbeddings:
    """H3 — With SEAM_SEMANTIC=on and embeddings, a semantic-only symbol surfaces."""

    def test_semantic_only_symbol_surfaces_in_search(self, tmp_path: Path) -> None:
        """A symbol with no keyword match but high semantic similarity appears in results.

        This is the core recall improvement: 'retry logic' should find
        '_backoff_with_jitter' even though none of the query tokens match the name.
        """
        from seam.query.engine import search

        conn = init_db(tmp_path / "test.db")
        test_model = "test-embed-model"

        # Keyword-only symbol: matches 'retry' via FTS
        _insert_symbol(conn, "retry_request", "Retry a failed HTTP request.")

        # Semantic-only symbol: no 'retry' token but semantically related
        sem_id = _insert_symbol(
            conn,
            "_backoff_with_jitter",
            "Delay policy with jitter.",
        )
        # Give it a vector that closely matches the query direction
        _insert_embedding(conn, sem_id, _f32([1.0, 0.0, 0.0]), model=test_model)

        # Query vector in the same direction → semantic similarity = 1.0
        query_vec = _f32([1.0, 0.0, 0.0])

        with patch("seam.config.SEAM_SEMANTIC", "on"):
            with patch("seam.config.SEAM_EMBED_MODEL", test_model):
                with patch("seam.query.semantic.is_available", return_value=True):
                    with patch("seam.query.semantic.embed_query", return_value=query_vec):
                        results = search(conn, "retry logic", limit=10)

        conn.close()
        result_names = [r["symbol"] for r in results]
        # The semantic-only symbol must appear in the hybrid result
        assert "_backoff_with_jitter" in result_names
        semantic_hit = next(r for r in results if r["symbol"] == "_backoff_with_jitter")
        assert semantic_hit["retrieval_mode"] == "semantic-only"
        assert semantic_hit["retrieval"]["semantic_score"] == 1.0
        assert any("discovery lead" in caveat for caveat in semantic_hit["caveats"])
        assert "seam_context" in semantic_hit["recommended_next_calls"]

    def test_semantic_only_symbol_surfaces_in_query(self, tmp_path: Path) -> None:
        """query() also surfaces semantic-only symbol when hybrid is enabled."""
        from seam.query.engine import query

        conn = init_db(tmp_path / "test.db")
        test_model = "test-embed-model"

        # This symbol has no keyword overlap with "cache mechanism"
        sem_id = _insert_symbol(conn, "_lru_store", "LRU eviction store for memoization.")
        _insert_embedding(conn, sem_id, _f32([1.0, 0.0, 0.0]), model=test_model)
        _insert_symbol(conn, "_memoize_helper", "Helper used by the LRU store.")
        conn.execute(
            "INSERT INTO edges (source_name, target_name, kind, file_id, line, confidence) "
            "SELECT '_lru_store', '_memoize_helper', 'call', id, 2, 'EXTRACTED' "
            "FROM files WHERE path = '/proj/a.py'"
        )
        conn.commit()

        # Also add a keyword-matching symbol for FTS seed
        _insert_symbol(conn, "cache_get", "Get from cache.")

        query_vec = _f32([1.0, 0.0, 0.0])

        with patch("seam.config.SEAM_SEMANTIC", "on"):
            with patch("seam.config.SEAM_EMBED_MODEL", test_model):
                with patch("seam.query.semantic.is_available", return_value=True):
                    with patch("seam.query.semantic.embed_query", return_value=query_vec):
                        results = query(conn, "cache mechanism", limit=10)

        conn.close()
        result_names = [r["symbol"] for r in results]
        assert "_lru_store" in result_names
        semantic_seed = next(r for r in results if r["symbol"] == "_lru_store")
        assert semantic_seed["retrieval_mode"] == "semantic-only"
        assert semantic_seed["retrieval"]["sources"] == ["semantic"]
        assert any("discovery lead" in caveat for caveat in semantic_seed["caveats"])
        semantic_neighbor = next(r for r in results if r["symbol"] == "_memoize_helper")
        assert semantic_neighbor["retrieval_mode"] == "graph-expanded-from-semantic"
        assert "semantic" in semantic_neighbor["retrieval"]["sources"]
        assert "graph" in semantic_neighbor["retrieval"]["sources"]
        assert any("discovery lead" in caveat for caveat in semantic_neighbor["caveats"])


# ── H4: hybrid merge: keyword hits are never dropped ─────────────────────────


class TestKeywordHitsPreserved:
    """H4 — Semantic ADDS recall; keyword hits must never be removed."""

    def test_fts_hit_still_present_when_semantic_on(self, tmp_path: Path) -> None:
        """A FTS5 match is present in hybrid results even when semantic is enabled."""
        from seam.query.engine import search

        conn = init_db(tmp_path / "test.db")

        # keyword_sym exactly matches the query via FTS
        _insert_symbol(conn, "keyword_sym", "Matches the query exactly.")

        # semantic_sym only matches via embeddings
        sem_id = _insert_symbol(conn, "semantic_sym", "Semantically related content.")
        _insert_embedding(conn, sem_id, _f32([0.9, 0.1, 0.0]))

        query_vec = _f32([1.0, 0.0, 0.0])

        with patch("seam.config.SEAM_SEMANTIC", "on"):
            with patch("seam.query.semantic.is_available", return_value=True):
                with patch("seam.query.semantic.embed_query", return_value=query_vec):
                    results = search(conn, "keyword_sym", limit=20)

        conn.close()
        result_names = [r["symbol"] for r in results]
        # The keyword match must still be present — hybrid never drops FTS hits
        assert "keyword_sym" in result_names
        keyword_hit = next(r for r in results if r["symbol"] == "keyword_sym")
        assert keyword_hit["retrieval_mode"] in {"lexical", "hybrid"}

    def test_query_fts_hit_still_present_when_semantic_on(self, tmp_path: Path) -> None:
        """query() FTS hits are preserved when semantic is enabled."""
        from seam.query.engine import query

        conn = init_db(tmp_path / "test.db")

        _insert_symbol(conn, "keyword_fn", "A function that matches the keyword query.")
        sem_id = _insert_symbol(conn, "semantic_fn", "Semantically related but no keywords.")
        _insert_embedding(conn, sem_id, _f32([0.9, 0.1]))

        query_vec = _f32([0.9, 0.1])

        with patch("seam.config.SEAM_SEMANTIC", "on"):
            with patch("seam.query.semantic.is_available", return_value=True):
                with patch("seam.query.semantic.embed_query", return_value=query_vec):
                    results = query(conn, "keyword_fn", limit=20)

        conn.close()
        result_names = [r["symbol"] for r in results]
        assert "keyword_fn" in result_names
        keyword_hit = next(r for r in results if r["symbol"] == "keyword_fn")
        assert keyword_hit["retrieval_mode"] in {"lexical", "hybrid"}


# ── H5: model mismatch → falls back to FTS5 ──────────────────────────────────


class TestModelMismatchFallback:
    """H5 — With SEAM_SEMANTIC=on but model mismatch, falls back to FTS5-only."""

    def test_search_falls_back_on_model_mismatch(self, tmp_path: Path) -> None:
        """When stored model != config model, FTS5 results still returned."""
        from seam.query.engine import search

        conn = init_db(tmp_path / "test.db")

        kw_id = _insert_symbol(conn, "process_event", "Process an event.")
        # Embed with model-A
        _insert_embedding(conn, kw_id, _f32([1.0, 0.0]), model="model-A")

        query_vec = _f32([1.0, 0.0])

        # Config model is model-B → mismatch → semantic returns [] → FTS only
        with patch("seam.config.SEAM_SEMANTIC", "on"):
            with patch("seam.config.SEAM_EMBED_MODEL", "model-B"):
                with patch("seam.query.semantic.is_available", return_value=True):
                    with patch("seam.query.semantic.embed_query", return_value=query_vec):
                        results = search(conn, "process_event", limit=10)

        conn.close()
        # FTS still finds the symbol via keyword match
        assert any(r["symbol"] == "process_event" for r in results)
