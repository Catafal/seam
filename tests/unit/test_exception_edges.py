from __future__ import annotations

from pathlib import Path

from seam.indexer.db import init_db
from seam.indexer.exceptions import extract_exception_edges
from seam.indexer.graph import extract_symbols
from seam.indexer.parser import parse_javascript, parse_python, parse_typescript
from seam.indexer.pipeline import index_one_file


def _edges_by_kind(path: Path, source: str, language: str) -> list[dict[str, object]]:
    path.write_text(source, encoding="utf-8")
    if language == "python":
        root = parse_python(path)
    elif language == "javascript":
        root = parse_javascript(path)
    else:
        root = parse_typescript(path)
    assert root is not None
    symbols = extract_symbols(root, language, path)
    edges = extract_exception_edges(root, language, path, symbols)
    return sorted(
        [
            {
                "source": edge["source"],
                "target": edge["target"],
                "kind": edge["kind"],
                "line": edge["line"],
                "confidence": edge["confidence"],
            }
            for edge in edges
        ],
        key=lambda item: (str(item["source"]), str(item["kind"]), str(item["target"]), int(item["line"])),
    )


def test_python_explicit_raises_and_catches_are_exception_edges(tmp_path: Path) -> None:
    path = tmp_path / "service.py"

    edges = _edges_by_kind(
        path,
        "\n".join(
            [
                "class CustomError(Exception):",
                "    pass",
                "",
                "def load(flag):",
                "    try:",
                "        if flag:",
                "            raise CustomError('bad')",
                "        raise external.OtherError",
                "    except (CustomError, ValueError):",
                "        raise",
                "    except RuntimeError as exc:",
                "        return None",
                "",
            ]
        ),
        "python",
    )

    assert edges == [
        {
            "source": "load",
            "target": "CustomError",
            "kind": "catches",
            "line": 9,
            "confidence": "EXTRACTED",
        },
        {
            "source": "load",
            "target": "RuntimeError",
            "kind": "catches",
            "line": 11,
            "confidence": "INFERRED",
        },
        {
            "source": "load",
            "target": "ValueError",
            "kind": "catches",
            "line": 9,
            "confidence": "INFERRED",
        },
        {
            "source": "load",
            "target": "CustomError",
            "kind": "raises",
            "line": 7,
            "confidence": "EXTRACTED",
        },
        {
            "source": "load",
            "target": "external.OtherError",
            "kind": "raises",
            "line": 8,
            "confidence": "INFERRED",
        },
    ]


def test_javascript_and_typescript_constructed_throws_are_raise_edges(tmp_path: Path) -> None:
    js_edges = _edges_by_kind(
        tmp_path / "client.js",
        "\n".join(
            [
                "class CustomError extends Error {}",
                "function request(kind) {",
                "  if (kind === 'custom') throw new CustomError('bad');",
                "  if (kind === 'native') throw Error('bad');",
                "  if (kind === 'remote') throw new Remote.ApiError('bad');",
                "  throw 'bad';",
                "}",
            ]
        ),
        "javascript",
    )
    ts_edges = _edges_by_kind(
        tmp_path / "client.ts",
        "\n".join(
            [
                "class ApiError extends Error {}",
                "export function request(kind: string) {",
                "  if (kind === 'api') throw new ApiError('bad');",
                "  const err = new Error('bad');",
                "  throw err;",
                "}",
            ]
        ),
        "typescript",
    )

    assert js_edges == [
        {
            "source": "request",
            "target": "CustomError",
            "kind": "raises",
            "line": 3,
            "confidence": "EXTRACTED",
        },
        {
            "source": "request",
            "target": "Error",
            "kind": "raises",
            "line": 4,
            "confidence": "INFERRED",
        },
        {
            "source": "request",
            "target": "Remote.ApiError",
            "kind": "raises",
            "line": 5,
            "confidence": "INFERRED",
        },
    ]
    assert ts_edges == [
        {
            "source": "request",
            "target": "ApiError",
            "kind": "raises",
            "line": 3,
            "confidence": "EXTRACTED",
        }
    ]


def test_indexer_persists_exception_edges_for_graph_tools(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    src = root / "service.py"
    src.write_text(
        "class CustomError(Exception):\n"
        "    pass\n"
        "\n"
        "def load(flag):\n"
        "    try:\n"
        "        raise CustomError('bad')\n"
        "    except CustomError:\n"
        "        return None\n",
        encoding="utf-8",
    )
    db_path = root / ".seam" / "seam.db"
    db_path.parent.mkdir()
    conn = init_db(db_path)

    assert index_one_file(conn, src) == (2, 4)

    rows = conn.execute(
        """
        SELECT e.source_name, e.target_name, e.kind, e.line, e.confidence
        FROM edges e
        JOIN files f ON f.id = e.file_id
        WHERE e.kind IN ('raises', 'catches')
        ORDER BY e.kind, e.line
        """
    ).fetchall()
    assert [dict(row) for row in rows] == [
        {
            "source_name": "load",
            "target_name": "CustomError",
            "kind": "catches",
            "line": 7,
            "confidence": "EXTRACTED",
        },
        {
            "source_name": "load",
            "target_name": "CustomError",
            "kind": "raises",
            "line": 6,
            "confidence": "EXTRACTED",
        },
    ]
