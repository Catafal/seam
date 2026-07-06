"""Semantic discovery product contract tests.

These tests pin the agent-facing contract without requiring a real embedding
model. The semantic read path must explain readiness and retrieval evidence
while keeping similarity out of graph semantics.
"""

from __future__ import annotations

import sqlite3
import struct
from pathlib import Path

from seam.indexer.db import init_db


def _f32(values: list[float]) -> bytes:
    return struct.pack(f"{len(values)}f", *values)


def _insert_symbol_with_embedding(
    conn: sqlite3.Connection,
    name: str,
    *,
    model: str = "test-model",
) -> int:
    conn.execute(
        "INSERT OR IGNORE INTO files (path, language, file_hash, mtime, indexed_at) "
        "VALUES ('/repo/app.py', 'python', 'hash', 1.0, 1.0)"
    )
    file_id = conn.execute("SELECT id FROM files WHERE path = '/repo/app.py'").fetchone()["id"]
    conn.execute(
        "INSERT INTO symbols (file_id, name, kind, start_line, end_line) "
        "VALUES (?, ?, 'function', 1, 2)",
        (file_id, name),
    )
    symbol_id = conn.execute("SELECT id FROM symbols WHERE name = ?", (name,)).fetchone()["id"]
    conn.execute(
        "INSERT INTO embeddings (symbol_id, model, dim, vector) VALUES (?, ?, 2, ?)",
        (symbol_id, model, _f32([1.0, 0.0])),
    )
    conn.commit()
    return int(symbol_id)


def test_semantic_readiness_reports_keyword_only_override(tmp_path: Path, monkeypatch) -> None:
    from seam.query.semantic_contract import semantic_readiness

    conn = init_db(tmp_path / "seam.db")
    monkeypatch.setattr("seam.config.SEAM_SEMANTIC", "on")

    status = semantic_readiness(conn, requested=False)

    conn.close()
    assert status["status"] == "keyword_only"
    assert status["usable"] is False
    assert status["reason"] == "keyword_only_override"


def test_semantic_readiness_reports_disabled_config(tmp_path: Path, monkeypatch) -> None:
    from seam.query.semantic_contract import semantic_readiness

    conn = init_db(tmp_path / "seam.db")
    monkeypatch.setattr("seam.config.SEAM_SEMANTIC", "off")

    status = semantic_readiness(conn, requested=True)

    conn.close()
    assert status["status"] == "disabled"
    assert status["usable"] is False
    assert status["reason"] == "config_off"


def test_semantic_readiness_reports_no_embeddings(tmp_path: Path, monkeypatch) -> None:
    from seam.query.semantic_contract import semantic_readiness

    conn = init_db(tmp_path / "seam.db")
    monkeypatch.setattr("seam.config.SEAM_SEMANTIC", "on")

    status = semantic_readiness(conn, requested=True, availability_check=lambda: True)

    conn.close()
    assert status["status"] == "unavailable"
    assert status["reason"] == "no_embeddings"
    assert status["embedding_count"] == 0


def test_semantic_readiness_reports_model_mismatch(tmp_path: Path, monkeypatch) -> None:
    from seam.query.semantic_contract import semantic_readiness

    conn = init_db(tmp_path / "seam.db")
    _insert_symbol_with_embedding(conn, "uses_old_model", model="old-model")
    monkeypatch.setattr("seam.config.SEAM_SEMANTIC", "on")
    monkeypatch.setattr("seam.config.SEAM_EMBED_MODEL", "new-model")

    status = semantic_readiness(conn, requested=True, availability_check=lambda: True)

    conn.close()
    assert status["status"] == "unavailable"
    assert status["reason"] == "model_mismatch"
    assert status["embedding_model_matches"] is False
    assert status["embedding_count"] == 1
    assert status["matching_embedding_count"] == 0


def test_semantic_readiness_reports_optional_extra_unavailable(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from seam.query.semantic_contract import semantic_readiness

    conn = init_db(tmp_path / "seam.db")
    _insert_symbol_with_embedding(conn, "has_vector", model="test-model")
    monkeypatch.setattr("seam.config.SEAM_SEMANTIC", "on")
    monkeypatch.setattr("seam.config.SEAM_EMBED_MODEL", "test-model")

    status = semantic_readiness(conn, requested=True, availability_check=lambda: False)

    conn.close()
    assert status["status"] == "unavailable"
    assert status["reason"] == "optional_extra_unavailable"
    assert status["usable"] is False


def test_semantic_readiness_reports_usable(tmp_path: Path, monkeypatch) -> None:
    from seam.query.semantic_contract import semantic_readiness

    conn = init_db(tmp_path / "seam.db")
    _insert_symbol_with_embedding(conn, "ready", model="test-model")
    monkeypatch.setattr("seam.config.SEAM_SEMANTIC", "on")
    monkeypatch.setattr("seam.config.SEAM_EMBED_MODEL", "test-model")

    status = semantic_readiness(conn, requested=True, availability_check=lambda: True)

    conn.close()
    assert status["status"] == "usable"
    assert status["reason"] is None
    assert status["usable"] is True
    assert status["matching_embedding_count"] == 1


def test_semantic_only_retrieval_contract_includes_caveat_and_next_calls() -> None:
    from seam.query.semantic_contract import retrieval_contract

    contract = retrieval_contract("semantic-only", semantic_score=0.92)

    assert contract["retrieval_mode"] == "semantic-only"
    assert contract["retrieval"]["semantic_score"] == 0.92
    assert any("discovery lead" in caveat for caveat in contract["caveats"])
    assert "seam_snippet" in contract["recommended_next_calls"]
    assert "seam_context" in contract["recommended_next_calls"]
