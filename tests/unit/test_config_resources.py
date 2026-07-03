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
        '  const note = "process.env.STRING_ONLY_TS";\n'
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


def test_compose_service_tracer_indexes_infra_without_values(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    compose = root / "docker-compose.yml"
    compose.write_text(
        "services:\n"
        "  api:\n"
        "    image: ghcr.io/example/api:1.0\n"
        "    build:\n"
        "      context: ./api\n"
        "      dockerfile: Dockerfile.api\n"
        "    ports:\n"
        '      - "8080:80/tcp"\n'
        '      - "${ADMIN_PORT}:9000"\n'
        "    environment:\n"
        "      DATABASE_URL: postgres://secret.invalid/app\n"
        '      FEATURE_FLAG: "true"\n'
        "  worker:\n"
        "    image: ghcr.io/example/worker:1.0\n",
        encoding="utf-8",
    )
    db = root / ".seam" / "seam.db"
    db.parent.mkdir()
    conn = init_db(db)
    try:
        index_one_file(conn, compose)

        resources = graph_search(
            conn,
            root=root,
            kind="resource",
            include_preview=True,
            preview_limit=10,
            sort="name",
        )
        resource_symbols = {item["symbol"]: item for item in resources["items"]}
        assert {
            "RESOURCE service API",
            "RESOURCE service WORKER",
            "RESOURCE image GHCR.IO/EXAMPLE/API_1.0",
            "RESOURCE dockerfile DOCKERFILE.API",
            "RESOURCE build_context ./API",
            "RESOURCE port 8080-80/TCP",
        }.issubset(resource_symbols)
        assert "RESOURCE port ${ADMIN_PORT}-9000" not in resource_symbols

        api_preview = {
            (preview["edge_kind"], preview["symbol"])
            for preview in resource_symbols["RESOURCE service API"]["preview"]
        }
        assert ("uses", "RESOURCE image GHCR.IO/EXAMPLE/API_1.0") in api_preview
        assert ("uses", "RESOURCE dockerfile DOCKERFILE.API") in api_preview
        assert ("uses", "RESOURCE port 8080-80/TCP") in api_preview
        assert ("configures", "CONFIG DATABASE_URL") in api_preview
        assert ("configures", "CONFIG FEATURE_FLAG") in api_preview

        schema = describe_schema(conn, root=root, verbose=True)
        assert schema["capabilities"]["has_infra_graph"] is True
        assert schema["breakdowns"]["resource_categories"]["service"] == 2
        assert schema["breakdowns"]["resource_categories"]["image"] == 2

        architecture = describe_architecture(conn, root=root, sections=["infra"], limit=20)
        assert architecture["sections"]["infra"]["status"] == "populated"
        infra_symbols = {item["symbol"] for item in architecture["sections"]["infra"]["items"]}
        assert "RESOURCE service API" in infra_symbols
        assert "RESOURCE dockerfile DOCKERFILE.API" in infra_symbols

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
                ("edges", "provenance"),
                ("config_keys", "key"),
                ("config_keys", "value_state"),
                ("config_keys", "value_category"),
                ("resources", "name"),
                ("resources", "normalized_name"),
                ("resources", "provenance"),
            )
            for row in conn.execute(f"SELECT {column} FROM {table}").fetchall()
            if row[0] is not None
        )
        assert "postgres://secret.invalid/app" not in stored_text
        assert "ghcr.io/example/api:1.0" in stored_text
    finally:
        conn.close()


def test_dockerfile_indexes_build_graph_without_values(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    dockerfile = root / "Dockerfile.api"
    dockerfile.write_text(
        "FROM --platform=$BUILDPLATFORM python:3.12 AS base\n"
        "ARG APP_ENV=production\n"
        "ENV DATABASE_URL=postgres://secret.invalid/app\n"
        "EXPOSE 8000/tcp 9000\n"
        "FROM base AS runner\n"
        "COPY --from=base /app /app\n",
        encoding="utf-8",
    )
    db = root / ".seam" / "seam.db"
    db.parent.mkdir()
    conn = init_db(db)
    try:
        index_one_file(conn, dockerfile)

        resources = graph_search(
            conn,
            root=root,
            kind="resource",
            include_preview=True,
            preview_limit=20,
            sort="name",
        )
        resource_symbols = {item["symbol"]: item for item in resources["items"]}
        assert {
            "RESOURCE dockerfile DOCKERFILE.API",
            "RESOURCE image PYTHON_3.12",
            "RESOURCE stage DOCKERFILE.API_BASE",
            "RESOURCE stage DOCKERFILE.API_RUNNER",
            "RESOURCE port 8000/TCP",
            "RESOURCE port 9000/TCP",
        }.issubset(resource_symbols)
        assert "RESOURCE image --PLATFORM_$BUILDPLATFORM" not in resource_symbols
        assert "RESOURCE image BASE" not in resource_symbols

        configs = graph_search(conn, root=root, kind="config", sort="name")
        assert [item["symbol"] for item in configs["items"]] == [
            "CONFIG APP_ENV",
            "CONFIG DATABASE_URL",
        ]

        base_preview = {
            (preview["edge_kind"], preview["symbol"])
            for preview in resource_symbols["RESOURCE stage DOCKERFILE.API_BASE"]["preview"]
        }
        assert ("uses", "RESOURCE image PYTHON_3.12") in base_preview
        assert ("configures", "CONFIG APP_ENV") in base_preview
        assert ("configures", "CONFIG DATABASE_URL") in base_preview
        assert ("uses", "RESOURCE port 8000/TCP") in base_preview

        runner_preview = {
            (preview["edge_kind"], preview["symbol"])
            for preview in resource_symbols["RESOURCE stage DOCKERFILE.API_RUNNER"]["preview"]
        }
        assert ("uses", "RESOURCE stage DOCKERFILE.API_BASE") in runner_preview

        stored_text = "\n".join(
            str(row[0])
            for table, column in (
                ("symbols", "name"),
                ("symbols", "signature"),
                ("symbols_fts", "search_text"),
                ("edges", "source_name"),
                ("edges", "target_name"),
                ("config_keys", "key"),
                ("config_keys", "value_state"),
                ("resources", "name"),
            )
            for row in conn.execute(f"SELECT {column} FROM {table}").fetchall()
            if row[0] is not None
        )
        assert "postgres://secret.invalid/app" not in stored_text
    finally:
        conn.close()


def test_compose_dependencies_env_files_volumes_and_networks(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    compose = root / "compose.yaml"
    compose.write_text(
        "services:\n"
        "  api:\n"
        "    depends_on:\n"
        "      db:\n"
        "        condition: service_healthy\n"
        "    env_file:\n"
        "      - .env.example\n"
        "      - ${RUNTIME_ENV_FILE}\n"
        "    volumes:\n"
        "      - app-data:/data\n"
        "      - ./src:/app/src\n"
        "    networks:\n"
        "      - web\n"
        "  db:\n"
        "    image: postgres:16\n"
        "volumes:\n"
        "  app-data:\n"
        "networks:\n"
        "  web:\n",
        encoding="utf-8",
    )
    db = root / ".seam" / "seam.db"
    db.parent.mkdir()
    conn = init_db(db)
    try:
        index_one_file(conn, compose)

        resources = graph_search(
            conn,
            root=root,
            kind="resource",
            include_preview=True,
            preview_limit=20,
            sort="name",
        )
        resource_symbols = {item["symbol"]: item for item in resources["items"]}
        assert {
            "RESOURCE service API",
            "RESOURCE service DB",
            "RESOURCE env_file .ENV.EXAMPLE",
            "RESOURCE volume APP-DATA",
            "RESOURCE network WEB",
        }.issubset(resource_symbols)
        assert "RESOURCE service CONDITION" not in resource_symbols
        assert "RESOURCE env_file ${RUNTIME_ENV_FILE}" not in resource_symbols
        assert "RESOURCE volume ./SRC" not in resource_symbols

        api_preview = {
            (preview["edge_kind"], preview["symbol"])
            for preview in resource_symbols["RESOURCE service API"]["preview"]
        }
        assert ("uses", "RESOURCE service DB") in api_preview
        assert ("uses", "RESOURCE env_file .ENV.EXAMPLE") in api_preview
        assert ("uses", "RESOURCE volume APP-DATA") in api_preview
        assert ("uses", "RESOURCE network WEB") in api_preview
    finally:
        conn.close()
