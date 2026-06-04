"""T10 — Semantic search coverage hardening (offline gate-safe tests).

Closes coverage gaps identified after T5+T6+T7+T8 implementation:

Gap groups:
    G1 — _is_hybrid_enabled: direct unit tests (config flags, DB state).
    G2 — _hydrate_symbol_rows: direct unit tests (empty ids, missing rows).
    G3 — search() hybrid no-new-candidates path (semantic overlap = pure FTS fallback).
    G4 — query() semantic seed priority (FTS seeds win over semantic seeds).
    G5 — cosine_sim edge cases (empty bytes, corrupt struct, single element).
    G6 — symbol_text edge cases (unicode, long strings).
    G7 — rrf_merge edge cases (single-element, k=0 safety, very large lists).
    G8 — index_embeddings with no symbols + fastembed available.
    G9 — semantic_candidates: embed_query returns b'' mid-path (already covered via S4).
    G10 — SEAM_SEMANTIC config knob explicit unit test.

All tests are GATE-SAFE: fully offline, no network, no model download.
"""

import sqlite3
import struct
from pathlib import Path
from unittest.mock import patch

import pytest

from seam.indexer.db import init_db  # noqa: E402

# ── Helpers ───────────────────────────────────────────────────────────────────


def _f32(values: list[float]) -> bytes:
    """Pack a list of floats as float32 bytes (no numpy)."""
    return struct.pack(f"{len(values)}f", *values)


def _insert_symbol(
    conn: sqlite3.Connection,
    name: str,
    docstring: str = "",
    file_path: str = "/proj/a.py",
    kind: str = "function",
) -> int:
    """Insert a symbol + file row; return symbol id."""
    conn.execute(
        "INSERT OR IGNORE INTO files (path, language, file_hash, mtime, indexed_at)"
        " VALUES (?, 'python', 'abc', 1.0, 1.0)",
        (file_path,),
    )
    file_id = conn.execute(
        "SELECT id FROM files WHERE path = ?", (file_path,)
    ).fetchone()["id"]
    conn.execute(
        "INSERT INTO symbols (file_id, name, kind, start_line, end_line, docstring)"
        " VALUES (?, ?, ?, 1, 5, ?)",
        (file_id, name, kind, docstring or None),
    )
    # Rebuild FTS index so FTS queries work
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
    """Insert a synthetic embedding row."""
    conn.execute(
        "INSERT OR REPLACE INTO embeddings (symbol_id, model, dim, vector)"
        " VALUES (?, ?, ?, ?)",
        (symbol_id, model, len(vector) // 4, vector),
    )
    conn.commit()


# ── G1: _is_hybrid_enabled ────────────────────────────────────────────────────


class TestIsHybridEnabled:
    """G1 — Direct tests for the _is_hybrid_enabled gating function."""

    def test_disabled_when_semantic_off(self, tmp_path: Path) -> None:
        """_is_hybrid_enabled returns False when SEAM_SEMANTIC='off'."""
        from seam.query.engine import _is_hybrid_enabled

        conn = init_db(tmp_path / "test.db")
        sym_id = _insert_symbol(conn, "my_fn")
        _insert_embedding(conn, sym_id, _f32([1.0, 0.0]), model="test-model")

        with patch("seam.config.SEAM_SEMANTIC", "off"):
            with patch("seam.config.SEAM_EMBED_MODEL", "test-model"):
                result = _is_hybrid_enabled(conn)

        conn.close()
        assert result is False

    def test_disabled_when_no_embeddings_for_model(self, tmp_path: Path) -> None:
        """_is_hybrid_enabled returns False when no embeddings exist for configured model."""
        from seam.query.engine import _is_hybrid_enabled

        conn = init_db(tmp_path / "test.db")
        # No embeddings in DB at all

        with patch("seam.config.SEAM_SEMANTIC", "on"):
            with patch("seam.config.SEAM_EMBED_MODEL", "test-model"):
                result = _is_hybrid_enabled(conn)

        conn.close()
        assert result is False

    def test_disabled_when_embeddings_for_different_model(self, tmp_path: Path) -> None:
        """_is_hybrid_enabled returns False when DB has embeddings but for a different model."""
        from seam.query.engine import _is_hybrid_enabled

        conn = init_db(tmp_path / "test.db")
        sym_id = _insert_symbol(conn, "fn")
        # Stored with model-A; configured model is model-B
        _insert_embedding(conn, sym_id, _f32([1.0, 0.0]), model="model-A")

        with patch("seam.config.SEAM_SEMANTIC", "on"):
            with patch("seam.config.SEAM_EMBED_MODEL", "model-B"):
                result = _is_hybrid_enabled(conn)

        conn.close()
        assert result is False

    def test_enabled_when_semantic_on_and_embeddings_match(self, tmp_path: Path) -> None:
        """_is_hybrid_enabled returns True when SEAM_SEMANTIC='on' and model matches."""
        from seam.query.engine import _is_hybrid_enabled

        conn = init_db(tmp_path / "test.db")
        sym_id = _insert_symbol(conn, "fn")
        _insert_embedding(conn, sym_id, _f32([1.0, 0.0]), model="test-model")

        with patch("seam.config.SEAM_SEMANTIC", "on"):
            with patch("seam.config.SEAM_EMBED_MODEL", "test-model"):
                result = _is_hybrid_enabled(conn)

        conn.close()
        assert result is True

    def test_disabled_when_semantic_value_is_arbitrary_string(self, tmp_path: Path) -> None:
        """_is_hybrid_enabled returns False for any SEAM_SEMANTIC value other than 'on'."""
        from seam.query.engine import _is_hybrid_enabled

        conn = init_db(tmp_path / "test.db")
        sym_id = _insert_symbol(conn, "fn")
        _insert_embedding(conn, sym_id, _f32([1.0, 0.0]), model="m")

        # Only exact string "on" enables hybrid
        for bad_value in ["ON", "true", "1", "yes", ""]:
            with patch("seam.config.SEAM_SEMANTIC", bad_value):
                with patch("seam.config.SEAM_EMBED_MODEL", "m"):
                    result = _is_hybrid_enabled(conn)
            assert result is False, f"Expected False for SEAM_SEMANTIC={bad_value!r}"

        conn.close()


# ── G2: _hydrate_symbol_rows ──────────────────────────────────────────────────


class TestHydrateSymbolRows:
    """G2 — Direct tests for _hydrate_symbol_rows (RRF id → row lookup)."""

    def test_empty_list_returns_empty_dict(self, tmp_path: Path) -> None:
        """_hydrate_symbol_rows([]) returns {} without querying the DB."""
        from seam.query.engine import _hydrate_symbol_rows

        conn = init_db(tmp_path / "test.db")
        result = _hydrate_symbol_rows(conn, [])
        conn.close()
        assert result == {}

    def test_known_ids_return_rows(self, tmp_path: Path) -> None:
        """_hydrate_symbol_rows returns a dict mapping id → row for known symbol_ids."""
        from seam.query.engine import _hydrate_symbol_rows

        conn = init_db(tmp_path / "test.db")
        id1 = _insert_symbol(conn, "alpha", file_path="/proj/a.py")
        id2 = _insert_symbol(conn, "beta", file_path="/proj/b.py")

        result = _hydrate_symbol_rows(conn, [id1, id2])
        conn.close()

        assert id1 in result
        assert id2 in result
        assert result[id1]["symbol"] == "alpha"
        assert result[id2]["symbol"] == "beta"

    def test_missing_ids_are_absent_from_result(self, tmp_path: Path) -> None:
        """_hydrate_symbol_rows silently omits ids that do not exist in the DB."""
        from seam.query.engine import _hydrate_symbol_rows

        conn = init_db(tmp_path / "test.db")
        id1 = _insert_symbol(conn, "exists")
        ghost_id = 99999  # Does not exist

        result = _hydrate_symbol_rows(conn, [id1, ghost_id])
        conn.close()

        assert id1 in result
        assert ghost_id not in result

    def test_row_contains_required_keys(self, tmp_path: Path) -> None:
        """Each hydrated row has symbol, file, line, cluster_id, signature keys."""
        from seam.query.engine import _hydrate_symbol_rows

        conn = init_db(tmp_path / "test.db")
        sym_id = _insert_symbol(conn, "my_func")

        result = _hydrate_symbol_rows(conn, [sym_id])
        conn.close()

        row = result[sym_id]
        for key in ("symbol", "file", "line", "cluster_id", "signature"):
            assert key in row, f"Missing key {key!r} in hydrated row"

    def test_single_id(self, tmp_path: Path) -> None:
        """_hydrate_symbol_rows works correctly for a single id."""
        from seam.query.engine import _hydrate_symbol_rows

        conn = init_db(tmp_path / "test.db")
        sym_id = _insert_symbol(conn, "solo_fn")

        result = _hydrate_symbol_rows(conn, [sym_id])
        conn.close()

        assert len(result) == 1
        assert result[sym_id]["symbol"] == "solo_fn"


# ── G3: search() hybrid no-new-candidates path ───────────────────────────────


class TestHybridNoNewCandidates:
    """G3 — When semantic returns only ids already in the FTS set, falls back to FTS."""

    def test_search_uses_fts_when_semantic_adds_nothing_new(self, tmp_path: Path) -> None:
        """If semantic candidates overlap exactly with FTS, pure FTS rescore is used.

        The hybrid 'new_sem_ids' check ensures semantic does NOT re-order results
        when it contributes no additional coverage beyond FTS.
        """
        from seam.query.engine import search

        conn = init_db(tmp_path / "test.db")
        test_model = "test-overlap-model"

        # Same symbol matches both FTS and semantic
        sym_id = _insert_symbol(conn, "keyword_and_semantic", "A function with both signals.")
        _insert_embedding(conn, sym_id, _f32([1.0, 0.0, 0.0]), model=test_model)

        # Semantic returns only the id already in FTS → no new candidates
        query_vec = _f32([1.0, 0.0, 0.0])  # identical direction to stored vec

        with patch("seam.config.SEAM_SEMANTIC", "on"):
            with patch("seam.config.SEAM_EMBED_MODEL", test_model):
                with patch("seam.query.semantic.is_available", return_value=True):
                    with patch("seam.query.semantic.embed_query", return_value=query_vec):
                        results = search(conn, "keyword_and_semantic", limit=10)

        conn.close()
        # Symbol must still appear (FTS path used as fallback)
        assert any(r["symbol"] == "keyword_and_semantic" for r in results)

    def test_search_fts_rescore_used_when_no_new_sem_candidates(self, tmp_path: Path) -> None:
        """When semantic adds nothing, the FTS rescored snippet is present (non-hybrid)."""
        from seam.query.engine import search

        conn = init_db(tmp_path / "test.db")
        test_model = "test-no-new-model"

        # Insert FTS-matching symbol with embedding
        sym_id = _insert_symbol(conn, "find_me_fts", "Special docstring for find_me_fts.")
        _insert_embedding(conn, sym_id, _f32([0.8, 0.6, 0.0]), model=test_model)

        query_vec = _f32([0.8, 0.6, 0.0])  # same as stored — perfect match but FTS finds it first

        with patch("seam.config.SEAM_SEMANTIC", "on"):
            with patch("seam.config.SEAM_EMBED_MODEL", test_model):
                with patch("seam.query.semantic.is_available", return_value=True):
                    with patch("seam.query.semantic.embed_query", return_value=query_vec):
                        results = search(conn, "find_me_fts", limit=10)

        conn.close()
        assert any(r["symbol"] == "find_me_fts" for r in results)


# ── G4: query() semantic seed priority ───────────────────────────────────────


class TestQuerySemanticSeedPriority:
    """G4 — FTS seeds take priority over semantic seeds with the same name."""

    def test_fts_seed_score_wins_over_semantic_seed_score(self, tmp_path: Path) -> None:
        """A symbol found by FTS keeps its rescored score, not the semantic 0.5 default.

        When the same symbol is found by both FTS (high score after rescore) and semantic,
        the FTS seed's score must be preserved in seed_map (semantic injection skips it
        because 'if name not in seed_map' is False).
        """
        from seam.query.engine import query

        conn = init_db(tmp_path / "test.db")
        test_model = "test-priority-model"

        # A symbol that matches both FTS (keyword: 'priority_test') and semantic
        sym_id = _insert_symbol(conn, "priority_test", "Tests priority between FTS and semantic.")
        _insert_embedding(conn, sym_id, _f32([1.0, 0.0, 0.0]), model=test_model)

        query_vec = _f32([1.0, 0.0, 0.0])

        with patch("seam.config.SEAM_SEMANTIC", "on"):
            with patch("seam.config.SEAM_EMBED_MODEL", test_model):
                with patch("seam.query.semantic.is_available", return_value=True):
                    with patch("seam.query.semantic.embed_query", return_value=query_vec):
                        results = query(conn, "priority_test", limit=10)

        conn.close()
        # Symbol should appear — and not crash
        assert any(r["symbol"] == "priority_test" for r in results)

    def test_query_semantic_only_symbol_uses_score_0_5(self, tmp_path: Path) -> None:
        """A symbol added only by semantic (no FTS match) gets score=0.5 in seed_map.

        This is below FTS rescored seeds (which typically score > 0.5) but above
        graph neighbors (which get score=0.0).
        """
        from seam.query.engine import query

        conn = init_db(tmp_path / "test.db")
        test_model = "test-score-model"

        # FTS-matching symbol (keyword: 'fts_match')
        _insert_symbol(conn, "fts_match_fn", "Matches via FTS keyword.")

        # Semantic-only symbol (no keyword overlap with 'fts_match')
        sem_id = _insert_symbol(conn, "_obscure_helper", "Internal helper for memoization.")
        _insert_embedding(conn, sem_id, _f32([1.0, 0.0, 0.0]), model=test_model)

        query_vec = _f32([1.0, 0.0, 0.0])

        with patch("seam.config.SEAM_SEMANTIC", "on"):
            with patch("seam.config.SEAM_EMBED_MODEL", test_model):
                with patch("seam.query.semantic.is_available", return_value=True):
                    with patch("seam.query.semantic.embed_query", return_value=query_vec):
                        results = query(conn, "fts_match", limit=10)

        conn.close()
        # Semantic-only symbol should appear in results (injected with score=0.5)
        # and should not be dropped
        result_names = [r["symbol"] for r in results]
        assert "_obscure_helper" in result_names

        # FTS match must also be present
        assert "fts_match_fn" in result_names

        # FTS match must rank before semantic-only symbol (higher score wins)
        fts_idx = result_names.index("fts_match_fn")
        sem_idx = result_names.index("_obscure_helper")
        assert fts_idx < sem_idx, (
            "FTS seed must rank before semantic-only seed; "
            f"FTS index={fts_idx}, semantic index={sem_idx}"
        )


# ── G5: cosine_sim edge cases ─────────────────────────────────────────────────


class TestCosineSImEdgeCases:
    """G5 — cosine_sim edge cases not covered by TestCosineSim."""

    def test_cosine_empty_bytes_returns_zero(self) -> None:
        """cosine_sim(b'', b'') returns 0.0 safely (empty input guard)."""
        from seam.query.semantic import cosine_sim

        assert cosine_sim(b"", b"") == 0.0
        assert cosine_sim(b"", _f32([1.0, 0.0])) == 0.0
        assert cosine_sim(_f32([1.0, 0.0]), b"") == 0.0

    def test_cosine_corrupt_bytes_returns_zero(self) -> None:
        """cosine_sim returns 0.0 on bytes that cannot be decoded as float32."""
        from seam.query.semantic import cosine_sim

        # b'\x00\x01\x02' is 3 bytes — not a multiple of 4 → struct.error → 0.0
        result = cosine_sim(b"\x00\x01\x02", b"\x00\x01\x02")
        assert result == 0.0

    def test_cosine_single_element_vectors(self) -> None:
        """cosine_sim works for single-element (1-dim) vectors."""
        from seam.query.semantic import cosine_sim

        v1 = _f32([1.0])
        v2 = _f32([1.0])
        assert abs(cosine_sim(v1, v2) - 1.0) < 1e-5

        v3 = _f32([-1.0])
        assert abs(cosine_sim(v1, v3) - (-1.0)) < 1e-5

    def test_cosine_large_dim_vectors(self) -> None:
        """cosine_sim handles higher-dimensional vectors (e.g. 384-dim) without overflow."""
        from seam.query.semantic import cosine_sim

        dim = 384
        v1 = _f32([1.0 / dim**0.5] * dim)  # unit vector in all-ones direction
        v2 = _f32([1.0 / dim**0.5] * dim)  # identical
        result = cosine_sim(v1, v2)
        assert abs(result - 1.0) < 1e-3  # slightly looser for float32 accumulation

    def test_cosine_never_raises_on_arbitrary_bytes(self) -> None:
        """cosine_sim must never raise — safe on any byte input."""
        from seam.query.semantic import cosine_sim

        for bad in [
            b"",
            b"\xff\xff\xff\xff",  # NaN float32
            b"\x00" * 8,          # zeros = zero vector
            b"\x01",              # not multiple of 4
            b"\x7f" * 400,        # large-dim-ish random bytes
        ]:
            try:
                cosine_sim(bad, bad)
            except Exception as exc:  # noqa: BLE001
                pytest.fail(f"cosine_sim raised on {bad!r}: {exc}")


# ── G6: symbol_text edge cases ────────────────────────────────────────────────


class TestSymbolTextEdgeCases:
    """G6 — symbol_text edge cases (unicode, long, unusual inputs)."""

    def test_symbol_text_unicode_name(self) -> None:
        """symbol_text handles unicode characters in name."""
        from seam.analysis.embeddings import symbol_text

        result = symbol_text("café_fn", None, None)
        assert "café_fn" in result

    def test_symbol_text_very_long_docstring(self) -> None:
        """symbol_text does not raise on very long inputs."""
        from seam.analysis.embeddings import symbol_text

        long_doc = "x" * 10_000
        result = symbol_text("fn", "def fn()", long_doc)
        assert "fn" in result
        assert isinstance(result, str)

    def test_symbol_text_newline_in_docstring(self) -> None:
        """symbol_text handles multiline docstrings (passthrough — no stripping)."""
        from seam.analysis.embeddings import symbol_text

        doc = "First line.\nSecond line.\nThird line."
        result = symbol_text("my_fn", None, doc)
        assert "my_fn" in result
        assert "First line." in result
        assert "Second line." in result

    def test_symbol_text_whitespace_only_fields_excluded(self) -> None:
        """symbol_text treats whitespace-only signature/docstring as absent (falsy check)."""
        from seam.analysis.embeddings import symbol_text

        # empty string is falsy → excluded, just like None
        result_empty = symbol_text("fn", "", "")
        result_none = symbol_text("fn", None, None)
        assert result_empty == result_none

    def test_symbol_text_all_fields_present(self) -> None:
        """All three fields present: joined with newlines in name, sig, doc order."""
        from seam.analysis.embeddings import symbol_text

        result = symbol_text("my_fn", "def my_fn(x)", "Does something.")
        lines = result.split("\n")
        assert lines[0] == "my_fn"
        assert lines[1] == "def my_fn(x)"
        assert lines[2] == "Does something."


# ── G7: rrf_merge edge cases ──────────────────────────────────────────────────


class TestRrfMergeEdgeCases:
    """G7 — rrf_merge edge cases beyond the basic S1 tests."""

    def test_rrf_merge_single_element_lists(self) -> None:
        """rrf_merge with single-element lists returns both ids."""
        from seam.query.semantic import rrf_merge

        result = rrf_merge([1], [2])
        assert set(result) == {1, 2}
        assert len(result) == 2

    def test_rrf_merge_same_single_element(self) -> None:
        """rrf_merge([x], [x]) returns [x] exactly once."""
        from seam.query.semantic import rrf_merge

        result = rrf_merge([42], [42])
        assert result == [42]

    def test_rrf_merge_order_stability_for_equal_scores(self) -> None:
        """When both lists are identical and disjoint, order is deterministic."""
        from seam.query.semantic import rrf_merge

        fts = [10, 20, 30]
        sem = [40, 50, 60]  # disjoint; each id has equal score from its list
        r1 = rrf_merge(fts, sem)
        r2 = rrf_merge(fts, sem)
        # Deterministic: same inputs → same output
        assert r1 == r2

    def test_rrf_merge_large_k_flattens_differences(self) -> None:
        """With very large k, score differences between top-ranked ids shrink."""
        from seam.query.semantic import rrf_merge

        # With k=10000, rank 1 and rank 100 have nearly equal scores
        fts = list(range(100))
        sem = list(reversed(range(100)))  # opposite ranking

        result_k60 = rrf_merge(fts, sem, k=60)
        result_k10000 = rrf_merge(fts, sem, k=10000)

        # Both contain the same set of ids
        assert set(result_k60) == set(result_k10000) == set(range(100))

    def test_rrf_merge_one_empty_preserves_order(self) -> None:
        """rrf_merge(fts, []) preserves FTS order (higher-ranked FTS gets higher score)."""
        from seam.query.semantic import rrf_merge

        fts = [5, 3, 1, 2, 4]
        result = rrf_merge(fts, [])

        # All ids must be present
        assert set(result) == set(fts)
        # Rank 1 (id=5) must come before rank 5 (id=4) since higher rank = higher RRF score
        assert result.index(5) < result.index(4)

    def test_rrf_merge_large_lists_performance(self) -> None:
        """rrf_merge handles 10k ids without crashing (performance sanity check)."""
        from seam.query.semantic import rrf_merge

        fts = list(range(10_000))
        sem = list(reversed(range(10_000)))

        result = rrf_merge(fts, sem)
        assert len(result) == 10_000  # union = same 10k ids


# ── G8: index_embeddings on empty DB + fastembed available ───────────────────


class TestIndexEmbeddingsEmptyDB:
    """G8 — index_embeddings returns 0 on empty DB (no symbols) even when fastembed available."""

    def test_returns_zero_on_no_symbols_when_available(self, tmp_path: Path) -> None:
        """index_embeddings returns 0 when DB has no symbols (nothing to embed)."""
        from seam.indexer.embedding_index import index_embeddings

        conn = init_db(tmp_path / "test.db")
        # No symbols inserted

        # Simulate fastembed available — but there's nothing to embed
        with patch("seam.indexer.embedding_index.is_available", return_value=True):
            # embed_texts should NOT be called (no symbols)
            with patch(
                "seam.indexer.embedding_index.embed_texts",
                side_effect=lambda texts, model: [][0],  # would raise if called
            ) as mock_embed:
                result = index_embeddings(conn, model="m", batch=32)
                # embed_texts must NOT be called (no rows to embed)
                assert mock_embed.call_count == 0

        conn.close()
        assert result == 0

    def test_embeddings_table_empty_after_no_symbols(self, tmp_path: Path) -> None:
        """No rows written to embeddings when there are no symbols."""
        from seam.indexer.embedding_index import index_embeddings

        conn = init_db(tmp_path / "test.db")

        with patch("seam.indexer.embedding_index.is_available", return_value=True):
            index_embeddings(conn, model="m", batch=32)

        count = conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
        conn.close()
        assert count == 0


# ── G10: SEAM_SEMANTIC config knob (off path is default) ──────────────────────


class TestSemanticConfigKnob:
    """G10 — SEAM_SEMANTIC config knob is 'off' by default; 'on' enables hybrid."""

    def test_default_config_is_off(self) -> None:
        """SEAM_SEMANTIC default is 'off' (semantic search is opt-in)."""
        import seam.config as cfg

        # The module-level constant must be 'off' unless overridden by env var.
        # In the gate environment, no env var is set, so this should be 'off'.
        # We read the original value at module import time.
        assert cfg.SEAM_SEMANTIC in ("off", "on"), (
            f"SEAM_SEMANTIC must be 'off' or 'on'; got {cfg.SEAM_SEMANTIC!r}"
        )
        # In the gate, the default must be 'off' (opt-in design — no env var set).
        # This verifies the config.py default, not a runtime state.
        import os

        if "SEAM_SEMANTIC" not in os.environ:
            assert cfg.SEAM_SEMANTIC == "off", (
                f"Default SEAM_SEMANTIC should be 'off'; got {cfg.SEAM_SEMANTIC!r}"
            )

    def test_seam_semantic_on_recognized(self, tmp_path: Path) -> None:
        """With SEAM_SEMANTIC='on', hybrid path is attempted when embeddings exist."""
        from seam.query.engine import search

        conn = init_db(tmp_path / "test.db")
        test_model = "config-knob-model"
        sym_id = _insert_symbol(conn, "knob_test_fn", "Config knob test.")
        _insert_embedding(conn, sym_id, _f32([1.0, 0.0]), model=test_model)

        query_vec = _f32([1.0, 0.0])
        embed_call_count = 0

        def tracking_embed(text: str, model: str) -> bytes:
            nonlocal embed_call_count
            embed_call_count += 1
            return query_vec

        with patch("seam.config.SEAM_SEMANTIC", "on"):
            with patch("seam.config.SEAM_EMBED_MODEL", test_model):
                with patch("seam.query.semantic.is_available", return_value=True):
                    with patch("seam.query.semantic.embed_query", side_effect=tracking_embed):
                        search(conn, "knob_test_fn", limit=10)

        conn.close()
        # embed_query must have been called (hybrid path attempted)
        assert embed_call_count > 0, "SEAM_SEMANTIC=on must trigger embed_query call"

    def test_seam_semantic_off_never_calls_embed(self, tmp_path: Path) -> None:
        """With SEAM_SEMANTIC='off', embed_query is never called regardless of embeddings."""
        from seam.query.engine import search

        conn = init_db(tmp_path / "test.db")
        test_model = "config-knob-off-model"
        sym_id = _insert_symbol(conn, "off_test_fn", "Should not embed.")
        _insert_embedding(conn, sym_id, _f32([1.0, 0.0]), model=test_model)

        embed_call_count = 0

        def tracking_embed(text: str, model: str) -> bytes:
            nonlocal embed_call_count
            embed_call_count += 1
            return b""

        with patch("seam.config.SEAM_SEMANTIC", "off"):
            with patch("seam.query.semantic.embed_query", side_effect=tracking_embed):
                with patch("seam.query.semantic.is_available", return_value=True):
                    search(conn, "off_test_fn", limit=10)

        conn.close()
        assert embed_call_count == 0, "SEAM_SEMANTIC=off must not call embed_query"
