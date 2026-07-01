from __future__ import annotations

from pathlib import Path

from seam.indexer.db import init_db
from seam.indexer.pipeline import index_one_file, walk_project
from seam.query.architecture import describe_architecture
from seam.query.graph_search import graph_search
from seam.query.schema import describe_schema


def test_env_example_indexes_config_and_resource_without_values(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    env = root / ".env.example"
    env.write_text(
        "DATABASE_URL=postgres://example.invalid/app\n"
        "OPENAI_API_KEY=sk-live-secret\n"
        "FEATURE_FLAG=true\n",
        encoding="utf-8",
    )
    db = root / ".seam" / "seam.db"
    db.parent.mkdir()
    conn = init_db(db)
    try:
        assert index_one_file(conn, env) == (4, 1)

        configs = graph_search(conn, root=root, kind="config", sort="name")
        resources = graph_search(conn, root=root, kind="resource", include_preview=True)

        assert [item["symbol"] for item in configs["items"]] == [
            "CONFIG DATABASE_URL",
            "CONFIG FEATURE_FLAG",
            "CONFIG OPENAI_API_KEY",
        ]
        assert resources["items"][0]["symbol"] == "RESOURCE database DATABASE"
        assert resources["items"][0]["preview"][0]["symbol"] == "CONFIG DATABASE_URL"
        assert resources["items"][0]["preview"][0]["edge_kind"] == "configures"

        text_columns = (
            ("files", "path"),
            ("files", "language"),
            ("symbols", "name"),
            ("symbols", "docstring"),
            ("symbols", "signature"),
            ("symbols", "decorators"),
            ("symbols", "qualified_name"),
            ("symbols", "search_text"),
            ("symbols_fts", "name"),
            ("symbols_fts", "docstring"),
            ("symbols_fts", "signature"),
            ("symbols_fts", "search_text"),
            ("edges", "source_name"),
            ("edges", "target_name"),
            ("edges", "kind"),
            ("edges", "receiver"),
            ("edges", "synthesized_by"),
            ("config_keys", "key"),
            ("config_keys", "normalized_key"),
            ("config_keys", "source_family"),
            ("config_keys", "role"),
            ("config_keys", "value_state"),
            ("config_keys", "value_category"),
            ("config_keys", "provenance"),
            ("resources", "name"),
            ("resources", "normalized_name"),
            ("resources", "category"),
            ("resources", "source_family"),
            ("resources", "provenance"),
        )
        stored_text = "\n".join(
            str(row[0])
            for table, column in text_columns
            for row in conn.execute(f"SELECT {column} FROM {table}").fetchall()
            if row[0] is not None
        )
        assert "postgres://example.invalid/app" not in stored_text
        assert "sk-live-secret" not in stored_text
    finally:
        conn.close()


def test_unsafe_env_file_is_skipped(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    unsafe = root / ".env"
    unsafe.write_text("DATABASE_URL=postgres://secret.invalid/app\n", encoding="utf-8")
    db = root / ".seam" / "seam.db"
    db.parent.mkdir()
    conn = init_db(db)
    try:
        assert index_one_file(conn, unsafe) is None
        assert unsafe not in walk_project(root)
        assert conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0] == 0
    finally:
        conn.close()


def test_python_and_typescript_config_reads_link_to_config_nodes(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    py = root / "settings.py"
    ts = root / "client.ts"
    py.write_text(
        "import os\n"
        "def connect():\n"
        "    return os.getenv('DATABASE_URL', 'postgres://secret.invalid/app')\n"
        "def required():\n"
        "    return os.environ['REDIS_URL']\n",
        encoding="utf-8",
    )
    ts.write_text(
        "export function endpoint() {\n"
        "  return process.env.API_URL;\n"
        "}\n"
        "export function publicKey() {\n"
        "  return import.meta.env.VITE_PUBLIC_KEY;\n"
        "}\n",
        encoding="utf-8",
    )
    db = root / ".seam" / "seam.db"
    db.parent.mkdir()
    conn = init_db(db)
    try:
        index_one_file(conn, py)
        index_one_file(conn, ts)

        reads = graph_search(
            conn,
            root=root,
            kind="config",
            edge_kind="reads_config",
            include_preview=True,
            sort="name",
        )

        assert reads["total"] == 4
        by_symbol = {item["symbol"]: item for item in reads["items"]}
        assert by_symbol["CONFIG API_URL"]["preview"][0]["symbol"] == "endpoint"
        assert by_symbol["CONFIG DATABASE_URL"]["preview"][0]["symbol"] == "connect"
        assert by_symbol["CONFIG REDIS_URL"]["preview"][0]["symbol"] == "required"
        assert by_symbol["CONFIG VITE_PUBLIC_KEY"]["preview"][0]["symbol"] == "publicKey"
        stored_text = "\n".join(
            str(row[0])
            for table, column in (
                ("symbols", "name"),
                ("symbols", "signature"),
                ("symbols", "search_text"),
                ("symbols_fts", "name"),
                ("symbols_fts", "signature"),
                ("symbols_fts", "search_text"),
                ("edges", "source_name"),
                ("edges", "target_name"),
                ("config_keys", "key"),
                ("config_keys", "value_state"),
                ("config_keys", "value_category"),
                ("resources", "name"),
            )
            for row in conn.execute(f"SELECT {column} FROM {table}").fetchall()
            if row[0] is not None
        )
        assert "postgres://secret.invalid/app" not in stored_text
    finally:
        conn.close()


def test_source_config_reads_ignore_comments_and_strings(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    py = root / "settings.py"
    ts = root / "client.ts"
    py.write_text(
        "# os.getenv('COMMENT_ONLY')\n"
        "def connect():\n"
        "    note = \"os.getenv('STRING_ONLY')\"\n"
        "    return os.getenv('DATABASE_URL')\n",
        encoding="utf-8",
    )
    ts.write_text(
        "// process.env.COMMENT_ONLY_TS\n"
        "export function endpoint() {\n"
        "  const note = \"process.env.STRING_ONLY_TS\";\n"
        "  return process.env.API_URL;\n"
        "}\n",
        encoding="utf-8",
    )
    db = root / ".seam" / "seam.db"
    db.parent.mkdir()
    conn = init_db(db)
    try:
        index_one_file(conn, py)
        index_one_file(conn, ts)

        configs = graph_search(conn, root=root, kind="config", sort="name")

        assert [item["symbol"] for item in configs["items"]] == [
            "CONFIG API_URL",
            "CONFIG DATABASE_URL",
        ]
    finally:
        conn.close()


def test_schema_reports_config_resource_capabilities(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    env = root / ".env.example"
    env.write_text("DATABASE_URL=postgres://example.invalid/app\n", encoding="utf-8")
    db = root / ".seam" / "seam.db"
    db.parent.mkdir()
    conn = init_db(db)
    try:
        index_one_file(conn, env)

        result = describe_schema(conn, root=root, verbose=True)

        assert result["schema_version"] >= 14
        assert result["counts"]["config_keys"] == 1
        assert result["counts"]["resources"] == 1
        assert result["breakdowns"]["symbol_kinds"]["config"] == 1
        assert result["breakdowns"]["symbol_kinds"]["resource"] == 1
        assert result["breakdowns"]["edge_kinds"]["configures"] == 1
        assert result["capabilities"]["has_config_keys_table"] is True
        assert result["capabilities"]["has_resources_table"] is True
        assert result["capabilities"]["has_config_nodes"] is True
        assert result["capabilities"]["has_resource_nodes"] is True
    finally:
        conn.close()


def test_architecture_reports_config_and_resource_sections(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    env = root / ".env.example"
    env.write_text("DATABASE_URL=postgres://example.invalid/app\n", encoding="utf-8")
    db = root / ".seam" / "seam.db"
    db.parent.mkdir()
    conn = init_db(db)
    try:
        index_one_file(conn, env)

        result = describe_architecture(
            conn,
            root=root,
            sections=["configs", "resources", "optional_surfaces"],
            limit=5,
        )

        assert result["counts"]["config_keys"] == 1
        assert result["counts"]["resources"] == 1
        assert result["sections"]["configs"]["status"] == "populated"
        assert result["sections"]["configs"]["items"][0]["key"] == "DATABASE_URL"
        assert result["sections"]["configs"]["items"][0]["value_state"] == "redacted"
        assert result["sections"]["resources"]["status"] == "populated"
        assert result["sections"]["resources"]["items"][0]["category"] == "database"
        assert "configs" not in result["sections"]["optional_surfaces"]
        assert "resources" not in result["sections"]["optional_surfaces"]
        assert {warning["code"] for warning in result["warnings"]}.isdisjoint(
            {"NO_CONFIG_EDGES", "NO_RESOURCE_EDGES"}
        )
    finally:
        conn.close()
