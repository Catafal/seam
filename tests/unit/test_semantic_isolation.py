"""Semantic discovery must stay isolated from graph/risk/doc surfaces."""

from __future__ import annotations

import json
import sqlite3
import struct
from pathlib import Path
from unittest.mock import patch

from seam.indexer.db import init_db, upsert_file
from seam.indexer.docs import extract_document
from seam.indexer.graph import Edge, Symbol
from seam.indexer.pipeline import index_one_file
from seam.server.tools import (
    handle_seam_graph_search,
    handle_seam_grounding,
    handle_seam_impact,
    handle_seam_suspects,
    handle_seam_trace,
)


def _symbol(name: str, file: Path, line: int) -> Symbol:
    return Symbol(
        name=name,
        kind="function",
        file=str(file),
        start_line=line,
        end_line=line + 1,
        docstring=f"{name} doc",
        signature=f"def {name}()",
        decorators=[],
        is_exported=True,
        visibility="public",
        qualified_name=name,
    )


def _edge(source: str, target: str, file: Path, line: int) -> Edge:
    return Edge(
        source=source,
        target=target,
        kind="call",
        file=str(file),
        line=line,
        confidence="EXTRACTED",
    )


def _make_repo(tmp_path: Path) -> tuple[sqlite3.Connection, Path]:
    root = tmp_path.resolve()
    src = root / "app.py"
    docs = root / "docs" / "prd.md"
    docs.parent.mkdir(parents=True)
    src.write_text(
        "def entry():\n    helper()\n\ndef helper():\n    return True\n",
        encoding="utf-8",
    )
    docs.write_text(
        "# Entry PRD\n\nThe implementation is `entry` in [app.py](../app.py).\n",
        encoding="utf-8",
    )
    db_path = root / ".seam" / "seam.db"
    db_path.parent.mkdir(parents=True)
    conn = init_db(db_path)
    upsert_file(
        conn,
        src,
        "python",
        "hash",
        [_symbol("entry", src, 1), _symbol("helper", src, 4)],
        [_edge("entry", "helper", src, 2)],
    )
    assert index_one_file(conn, docs, root=root) == (0, 0)
    extracted, refs = extract_document(docs, root, docs.read_text(encoding="utf-8"))
    assert extracted["anchors"]
    assert refs

    symbol_id = conn.execute("SELECT id FROM symbols WHERE name = 'entry'").fetchone()["id"]
    conn.execute(
        "INSERT OR REPLACE INTO embeddings (symbol_id, model, dim, vector) VALUES (?, ?, 2, ?)",
        (symbol_id, "test-model", struct.pack("2f", 1.0, 0.0)),
    )
    conn.commit()
    return conn, root


def _json_text(payload: object) -> str:
    return json.dumps(payload, sort_keys=True)


def test_semantic_metadata_is_isolated_to_search_and_query(tmp_path: Path, monkeypatch) -> None:
    conn, root = _make_repo(tmp_path)
    monkeypatch.setattr("seam.config.SEAM_SEMANTIC", "on")
    monkeypatch.setattr("seam.config.SEAM_EMBED_MODEL", "test-model")
    try:
        with patch("seam.query.semantic.embed_query", side_effect=AssertionError):
            graph = handle_seam_graph_search(conn, root, kind="function", name_pattern="entry")
            impact = handle_seam_impact(conn, "helper", root, direction="upstream")
            trace = handle_seam_trace(conn, "entry", "helper", root)
            grounding = handle_seam_grounding(conn, root, symbol="entry")
            suspects = handle_seam_suspects(conn, root, mode="symbols", target="entry")
    finally:
        conn.close()

    for payload in (graph, impact, trace, grounding, suspects):
        text = _json_text(payload)
        assert "semantic-only" not in text
        assert "retrieval_mode" not in text
        assert "semantic_score" not in text
