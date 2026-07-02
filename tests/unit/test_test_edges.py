from __future__ import annotations

from pathlib import Path

from seam.indexer.db import init_db
from seam.indexer.pipeline import index_one_file


def _edge_rows(conn) -> list[dict[str, object]]:
    rows = conn.execute(
        """
        SELECT e.source_name, e.target_name, e.kind, e.line, e.confidence, e.synthesized_by
        FROM edges e
        WHERE e.kind = 'tests'
        ORDER BY e.source_name, e.target_name, e.line
        """
    ).fetchall()
    return [dict(row) for row in rows]


def test_index_test_edges_materializes_exact_call_and_instantiation_evidence(
    tmp_path: Path,
) -> None:
    from seam.indexer.test_edges import index_test_edges

    root = tmp_path.resolve()
    src = root / "service.py"
    test_file = root / "tests" / "test_service.py"
    test_file.parent.mkdir()
    src.write_text(
        "class Client:\n"
        "    pass\n"
        "\n"
        "def parse_config():\n"
        "    return True\n",
        encoding="utf-8",
    )
    test_file.write_text(
        "from service import Client, parse_config\n"
        "\n"
        "def helper():\n"
        "    return parse_config()\n"
        "\n"
        "def test_parse_config():\n"
        "    client = Client()\n"
        "    assert parse_config()\n",
        encoding="utf-8",
    )

    db_path = root / ".seam" / "seam.db"
    db_path.parent.mkdir()
    conn = init_db(db_path)
    assert index_one_file(conn, src) is not None
    assert index_one_file(conn, test_file) is not None

    assert index_test_edges(conn) == 2

    assert _edge_rows(conn) == [
        {
            "source_name": "test_parse_config",
            "target_name": "Client",
            "kind": "tests",
            "line": 7,
            "confidence": "EXTRACTED",
            "synthesized_by": "test-instantiates",
        },
        {
            "source_name": "test_parse_config",
            "target_name": "parse_config",
            "kind": "tests",
            "line": 8,
            "confidence": "EXTRACTED",
            "synthesized_by": "test-call",
        },
    ]


def test_index_test_edges_skips_same_file_test_helpers_and_ambiguous_absent_targets(
    tmp_path: Path,
) -> None:
    from seam.indexer.test_edges import index_test_edges

    root = tmp_path.resolve()
    test_file = root / "tests" / "test_only.py"
    test_file.parent.mkdir()
    test_file.write_text(
        "def helper():\n"
        "    return True\n"
        "\n"
        "def test_helper():\n"
        "    assert helper()\n"
        "    assert missing_external()\n",
        encoding="utf-8",
    )

    db_path = root / ".seam" / "seam.db"
    db_path.parent.mkdir()
    conn = init_db(db_path)
    assert index_one_file(conn, test_file) is not None

    assert index_test_edges(conn) == 0
    assert _edge_rows(conn) == []


def test_test_edges_are_visible_in_schema_and_graph_search(tmp_path: Path) -> None:
    from seam.indexer.test_edges import index_test_edges
    from seam.query.graph_search import graph_search
    from seam.query.schema import describe_schema

    root = tmp_path.resolve()
    src = root / "maths.py"
    test_file = root / "tests" / "test_maths.py"
    test_file.parent.mkdir()
    src.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    test_file.write_text(
        "from maths import add\n"
        "\n"
        "def test_add():\n"
        "    assert add(1, 2) == 3\n",
        encoding="utf-8",
    )

    db_path = root / ".seam" / "seam.db"
    db_path.parent.mkdir()
    conn = init_db(db_path)
    assert index_one_file(conn, src) is not None
    assert index_one_file(conn, test_file) is not None
    assert index_test_edges(conn) == 1

    schema = describe_schema(conn, root=root)
    assert schema["breakdowns"]["edge_kinds"]["tests"] == 1
    assert schema["capabilities"]["has_test_edges"] is True

    result = graph_search(
        conn,
        root=root,
        edge_kind="tests",
        direction="incoming",
        include_preview=True,
    )
    assert "error" not in result
    assert result["total"] == 1
    assert result["items"][0]["symbol"] == "add"
    assert result["items"][0]["preview"][0]["edge_kind"] == "tests"
    assert result["items"][0]["preview"][0]["synthesized_by"] == "test-call"


def test_index_test_edges_materializes_name_matched_import_evidence_when_no_call_is_visible(
    tmp_path: Path,
) -> None:
    from seam.indexer.test_edges import index_test_edges

    root = tmp_path.resolve()
    src = root / "models.py"
    test_file = root / "tests" / "test_models.py"
    test_file.parent.mkdir()
    src.write_text("class User:\n    pass\n", encoding="utf-8")
    test_file.write_text(
        "from models import User\n"
        "\n"
        "def test_user_contract():\n"
        "    assert True\n",
        encoding="utf-8",
    )

    db_path = root / ".seam" / "seam.db"
    db_path.parent.mkdir()
    conn = init_db(db_path)
    assert index_one_file(conn, src) is not None
    assert index_one_file(conn, test_file) is not None

    assert index_test_edges(conn) == 1
    assert _edge_rows(conn) == [
        {
            "source_name": "test_user_contract",
            "target_name": "User",
            "kind": "tests",
            "line": 1,
            "confidence": "EXTRACTED",
            "synthesized_by": "test-import",
        }
    ]


def test_index_test_edges_skips_broad_imports_that_do_not_match_test_name(
    tmp_path: Path,
) -> None:
    from seam.indexer.test_edges import index_test_edges

    root = tmp_path.resolve()
    src = root / "models.py"
    test_file = root / "tests" / "test_models.py"
    test_file.parent.mkdir()
    src.write_text("class User:\n    pass\n", encoding="utf-8")
    test_file.write_text(
        "from models import User\n"
        "\n"
        "def test_contract():\n"
        "    assert True\n",
        encoding="utf-8",
    )

    db_path = root / ".seam" / "seam.db"
    db_path.parent.mkdir()
    conn = init_db(db_path)
    assert index_one_file(conn, src) is not None
    assert index_one_file(conn, test_file) is not None

    assert index_test_edges(conn) == 0
    assert _edge_rows(conn) == []


def test_index_test_edges_uses_name_proximity_only_for_unique_targets(tmp_path: Path) -> None:
    from seam.indexer.test_edges import index_test_edges

    root = tmp_path.resolve()
    src = root / "parser.py"
    other = root / "other_parser.py"
    test_file = root / "tests" / "test_parser.py"
    test_file.parent.mkdir()
    src.write_text("def parse_config():\n    return True\n", encoding="utf-8")
    other.write_text("def parse_other():\n    return True\n", encoding="utf-8")
    test_file.write_text(
        "def test_parse_config():\n"
        "    assert True\n"
        "\n"
        "def test_parse_missing():\n"
        "    assert True\n",
        encoding="utf-8",
    )

    db_path = root / ".seam" / "seam.db"
    db_path.parent.mkdir()
    conn = init_db(db_path)
    assert index_one_file(conn, src) is not None
    assert index_one_file(conn, other) is not None
    assert index_one_file(conn, test_file) is not None

    assert index_test_edges(conn) == 1
    assert _edge_rows(conn) == [
        {
            "source_name": "test_parse_config",
            "target_name": "parse_config",
            "kind": "tests",
            "line": 1,
            "confidence": "INFERRED",
            "synthesized_by": "test-name-proximity",
        }
    ]
