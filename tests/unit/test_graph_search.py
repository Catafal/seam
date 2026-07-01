"""Behavior tests for Phase 11 P1.3 — structural graph search."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from typer.testing import CliRunner

from seam.indexer.db import init_db, upsert_file
from seam.indexer.graph import Edge, Symbol


def _hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _sym(
    name: str,
    file: Path,
    *,
    kind: str = "function",
    start: int = 1,
    end: int = 2,
    signature: str | None = None,
    qualified_name: str | None = None,
    is_exported: bool | None = True,
    visibility: str | None = "public",
) -> Symbol:
    return Symbol(
        name=name,
        kind=kind,
        file=str(file),
        start_line=start,
        end_line=end,
        docstring=None,
        signature=signature or f"def {name}()",
        decorators=[],
        is_exported=is_exported,
        visibility=visibility,
        qualified_name=qualified_name or name,
    )


def _edge(
    source: str,
    target: str,
    file: Path,
    *,
    kind: str = "call",
    line: int = 1,
    confidence: str = "EXTRACTED",
    receiver: str | None = None,
    synthesized_by: str | None = None,
) -> Edge:
    edge = Edge(
        source=source,
        target=target,
        kind=kind,
        file=str(file),
        line=line,
        confidence=confidence,  # type: ignore[typeddict-item]
    )
    if receiver is not None:
        edge["receiver"] = receiver
    if synthesized_by is not None:
        edge["synthesized_by"] = synthesized_by
    return edge


def _make_graph_repo(tmp_path: Path) -> tuple[sqlite3.Connection, Path]:
    root = tmp_path.resolve()
    app = root / "app.py"
    models = root / "models.py"
    tests = root / "tests" / "test_app.py"
    tests.parent.mkdir()

    app_src = (
        "def entry():\n"
        "    helper()\n"
        "    writer()\n"
        "\n"
        "def helper():\n"
        "    return True\n"
        "\n"
        "def writer():\n"
        "    return True\n"
    )
    models_src = "class User:\n    pass\n"
    tests_src = "def test_entry():\n    entry()\n"
    app.write_text(app_src, encoding="utf-8")
    models.write_text(models_src, encoding="utf-8")
    tests.write_text(tests_src, encoding="utf-8")

    db_path = root / ".seam" / "seam.db"
    db_path.parent.mkdir()
    conn = init_db(db_path)
    upsert_file(
        conn,
        app,
        "python",
        _hash(app_src),
        [
            _sym("entry", app, start=1, end=3),
            _sym("helper", app, start=5, end=6),
            _sym("writer", app, start=8, end=9),
        ],
        [
            _edge("entry", "helper", app, line=2),
            _edge("entry", "writer", app, line=3, kind="writes", receiver="self"),
        ],
    )
    upsert_file(
        conn,
        models,
        "python",
        _hash(models_src),
        [_sym("User", models, kind="class", start=1, end=2)],
        [_edge("User", "object", models, line=1, kind="extends", confidence="INFERRED")],
    )
    upsert_file(
        conn,
        tests,
        "python",
        _hash(tests_src),
        [_sym("test_entry", tests, start=1, end=2)],
        [_edge("test_entry", "entry", tests, line=2)],
    )
    return conn, root


def test_graph_search_returns_paginated_symbol_results_with_degrees(tmp_path: Path) -> None:
    """The tracer path returns compact symbol records that chain into UID-based tools."""
    from seam.query.graph_search import graph_search

    conn, root = _make_graph_repo(tmp_path)

    result = graph_search(conn, root=root, kind="function", name_pattern="*er", limit=2)

    assert result["query"]["kind"] == "function"
    assert result["query"]["name_pattern"] == "*er"
    assert result["total"] == 2
    assert result["limit"] == 2
    assert result["offset"] == 0
    assert result["has_more"] is False
    assert result["warnings"] == []
    assert [item["symbol"] for item in result["items"]] == ["helper", "writer"]
    helper = result["items"][0]
    assert helper["uid"]
    assert helper["file"] == "app.py"
    assert helper["line"] == 5
    assert helper["degrees"] == {"incoming": 1, "outgoing": 0, "total": 1}
    assert "source" not in helper


def test_graph_search_dead_code_preset_compiles_to_incoming_call_filter(tmp_path: Path) -> None:
    """The dead-code preset finds symbols with no inbound call edges, not deletion-safe facts."""
    from seam.query.graph_search import graph_search

    conn, root = _make_graph_repo(tmp_path)

    result = graph_search(conn, root=root, kind="function", preset="dead-code", sort="name")

    assert result["query"]["preset"] == "dead-code"
    assert result["query"]["edge_kind"] == "call"
    assert result["query"]["direction"] == "incoming"
    assert result["query"]["max_in_degree"] == 0
    assert [item["symbol"] for item in result["items"]] == ["test_entry", "writer"]


def test_graph_search_isolates_preset_compiles_to_zero_total_degree(tmp_path: Path) -> None:
    """The isolates preset should find symbols with no matching graph relationships."""
    from seam.query.graph_search import graph_search

    conn, root = _make_graph_repo(tmp_path)

    result = graph_search(conn, root=root, preset="isolates")

    assert result["query"]["preset"] == "isolates"
    assert result["query"]["max_degree"] == 0
    assert [item["symbol"] for item in result["items"]] == []


def test_graph_search_edge_filters_and_sorting_use_filtered_degrees(tmp_path: Path) -> None:
    """Degree counts describe the selected edge kind instead of every stored relationship."""
    from seam.query.graph_search import graph_search

    conn, root = _make_graph_repo(tmp_path)

    result = graph_search(
        conn,
        root=root,
        edge_kind="writes",
        direction="outgoing",
        min_out_degree=1,
        sort="out-degree",
    )

    assert result["total"] == 1
    item = result["items"][0]
    assert item["symbol"] == "entry"
    assert item["degrees"] == {"incoming": 0, "outgoing": 1, "total": 1}


def test_graph_search_connected_preview_is_opt_in_and_capped(tmp_path: Path) -> None:
    """Previews are bounded one-hop metadata hints, not recursive context expansion."""
    from seam.query.graph_search import graph_search

    conn, root = _make_graph_repo(tmp_path)

    without_preview = graph_search(conn, root=root, name_pattern="entry")
    with_preview = graph_search(
        conn,
        root=root,
        name_pattern="entry",
        include_preview=True,
        preview_limit=1,
    )

    assert "preview" not in without_preview["items"][0]
    item = with_preview["items"][0]
    assert len(item["preview"]) == 1
    assert item["preview"][0]["edge_kind"] == "call"
    assert item["preview"][0]["direction"] in {"incoming", "outgoing"}
    assert item["preview_truncated"] is True
    assert any(w["code"] == "PREVIEW_TRUNCATED" for w in with_preview["warnings"])


def test_graph_search_rejects_invalid_filters(tmp_path: Path) -> None:
    from seam.query.graph_search import graph_search

    conn, root = _make_graph_repo(tmp_path)

    bad_kind = graph_search(conn, root=root, kind="route")
    bad_regex = graph_search(conn, root=root, name_pattern="[", regex=True)
    long_regex = graph_search(conn, root=root, name_pattern="a" * 300, regex=True)
    bad_edge = graph_search(conn, root=root, edge_kind="http_calls")

    assert bad_kind["error"] == "INVALID_INPUT"
    assert bad_regex["error"] == "INVALID_QUERY"
    assert long_regex["error"] == "INVALID_INPUT"
    assert bad_edge["error"] == "INVALID_INPUT"


def test_graph_search_does_not_leak_absolute_paths_outside_root(tmp_path: Path) -> None:
    """A mismatched root/db_dir should not expose local absolute paths in results."""
    from seam.query.graph_search import graph_search

    conn, _root = _make_graph_repo(tmp_path)
    other_root = tmp_path / "other"
    other_root.mkdir()

    result = graph_search(conn, root=other_root, kind="class")

    assert result["items"][0]["file"] == "<outside-root>/models.py"


def test_graph_search_handler_and_mcp_registration(tmp_path: Path) -> None:
    from seam.server.mcp import create_server
    from seam.server.tools import handle_seam_graph_search

    conn, root = _make_graph_repo(tmp_path)

    result = handle_seam_graph_search(conn, root, preset="hotspot", min_in_degree=1)

    assert result["query"]["preset"] == "hotspot"
    assert result["items"][0]["symbol"] == "entry"
    assert result["items"][0]["file"] == "app.py"

    server = create_server(conn, root)
    tool_names = list(server._tool_manager._tools.keys())
    assert "seam_graph_search" in tool_names
    assert len(tool_names) == 15


def test_graph_search_cli_json_and_quiet(tmp_path: Path) -> None:
    from seam.cli.main import app

    conn, root = _make_graph_repo(tmp_path)
    conn.close()
    runner = CliRunner()

    json_result = runner.invoke(
        app,
        [
            "graph-search",
            str(root),
            "--kind",
            "function",
            "--name",
            "*er",
            "--json",
        ],
    )
    assert json_result.exit_code == 0, json_result.output
    payload = json.loads(json_result.output)
    assert payload["ok"] is True
    assert [item["symbol"] for item in payload["data"]["items"]] == ["helper", "writer"]

    quiet_result = runner.invoke(
        app,
        ["graph-search", str(root), "--kind", "class", "--quiet"],
    )
    assert quiet_result.exit_code == 0, quiet_result.output
    assert quiet_result.output == "models.py:1:User\n"
