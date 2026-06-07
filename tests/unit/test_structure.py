"""Unit tests for seam/query/structure.py — Slice 1 tracer-bullet.

TDD: Tests written before implementation (red-green-refactor).

Test groups:
    S1 — Core tree shape: nesting, container roll-up, top-level funcs present,
         symbol counts, area=None, truncated=0.
    S2 — Handler: relativization, no absolute paths leak, correct StructureResult shape.
    S3 — CLI: --json envelope, NO_INDEX guard.

Fixtures build a tiny in-memory/temp SQLite index (same pattern as test_flows.py
and test_query_clusters.py). No mocking of the query layer — tests exercise the
actual SQL path end-to-end.
"""

import json
import sqlite3
import tempfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from seam.indexer.db import init_db, upsert_file
from seam.indexer.graph import Edge, Symbol

# ── Helpers ───────────────────────────────────────────────────────────────────


def _sym(
    name: str,
    file: str,
    kind: str = "function",
    line: int = 1,
    qualified_name: str | None = None,
) -> Symbol:
    """Build a Symbol TypedDict for test seeding."""
    return Symbol(
        name=name,
        kind=kind,
        file=file,
        start_line=line,
        end_line=line + 5,
        docstring=None,
        signature=None,
        decorators=[],
        is_exported=None,
        visibility=None,
        qualified_name=qualified_name or name,
    )


def _edge(source: str, target: str, file: str) -> Edge:
    return Edge(
        source=source,
        target=target,
        kind="call",
        file=file,
        line=1,
        confidence="EXTRACTED",
    )


@pytest.fixture()
def tmp_db():
    """Yield (conn, tmp_path) with an initialized DB + stub source files."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        db_path = tmp_path / ".seam" / "seam.db"
        db_path.parent.mkdir()
        conn = init_db(db_path)
        (tmp_path / "app.py").write_text("# stub\n")
        (tmp_path / "utils.py").write_text("# stub\n")
        (tmp_path / "subdir").mkdir()
        (tmp_path / "subdir" / "helper.py").write_text("# stub\n")
        yield conn, tmp_path, db_path
        conn.close()


def _seed_basic(conn: sqlite3.Connection, tmp_path: Path) -> None:
    """Seed a basic index:
    app.py          : MyClass (class), MyClass.do_thing (method), standalone_func (function)
    utils.py        : UtilClass (class), UtilClass.helper (method), top_level (function)
    subdir/helper.py: bare_func (function)
    """
    app = str(tmp_path / "app.py")
    utils = str(tmp_path / "utils.py")
    helper = str(tmp_path / "subdir" / "helper.py")

    # app.py symbols: class + method + top-level function
    upsert_file(
        conn,
        Path(app),
        "python",
        "h1",
        [
            _sym("MyClass", app, kind="class", line=1, qualified_name="MyClass"),
            _sym("MyClass.do_thing", app, kind="method", line=5, qualified_name="MyClass.do_thing"),
            _sym(
                "standalone_func", app, kind="function", line=15, qualified_name="standalone_func"
            ),
        ],
        [],
    )

    # utils.py symbols
    upsert_file(
        conn,
        Path(utils),
        "python",
        "h2",
        [
            _sym("UtilClass", utils, kind="class", line=1, qualified_name="UtilClass"),
            _sym(
                "UtilClass.helper", utils, kind="method", line=3, qualified_name="UtilClass.helper"
            ),
            _sym("top_level", utils, kind="function", line=10, qualified_name="top_level"),
        ],
        [],
    )

    # subdir/helper.py
    upsert_file(
        conn,
        Path(helper),
        "python",
        "h3",
        [
            _sym("bare_func", helper, kind="function", line=1, qualified_name="bare_func"),
        ],
        [],
    )


# ── S1: Core tree shape ────────────────────────────────────────────────────────


class TestBuildStructure:
    """S1: build_structure() returns the correct tree shape."""

    def test_returns_structure_result(self, tmp_db) -> None:
        """build_structure returns a dict with 'tree' and 'truncated' keys."""
        from seam.query.structure import build_structure

        conn, tmp_path, _ = tmp_db
        _seed_basic(conn, tmp_path)

        result = build_structure(conn, tmp_path)
        assert "tree" in result
        assert "truncated" in result

    def test_truncated_is_zero_slice1(self, tmp_db) -> None:
        """Slice 1: no caps, so truncated must always be 0."""
        from seam.query.structure import build_structure

        conn, tmp_path, _ = tmp_db
        _seed_basic(conn, tmp_path)

        result = build_structure(conn, tmp_path)
        assert result["truncated"] == 0

    def test_area_is_none_slice1(self, tmp_db) -> None:
        """Slice 1: area is None on every node (labels come in a later slice)."""
        from seam.query.structure import build_structure

        conn, tmp_path, _ = tmp_db
        _seed_basic(conn, tmp_path)

        def _all_areas(node: dict) -> list:
            areas = [node.get("area")]
            for child in node.get("children", []):
                areas.extend(_all_areas(child))
            for member_container in node.get("children", []):
                pass  # members is an int, not nodes
            return areas

        result = build_structure(conn, tmp_path)
        areas = _all_areas(result["tree"])
        assert all(a is None for a in areas)

    def test_root_node_is_dir_kind(self, tmp_db) -> None:
        """The root of the tree is a 'dir' node."""
        from seam.query.structure import build_structure

        conn, tmp_path, _ = tmp_db
        _seed_basic(conn, tmp_path)

        result = build_structure(conn, tmp_path)
        assert result["tree"]["kind"] == "dir"

    def test_file_nodes_present_under_root(self, tmp_db) -> None:
        """File nodes for app.py and utils.py appear as children of the root dir."""
        from seam.query.structure import build_structure

        conn, tmp_path, _ = tmp_db
        _seed_basic(conn, tmp_path)

        result = build_structure(conn, tmp_path)
        root = result["tree"]
        # app.py and utils.py should be direct children (or in a subdir child)
        names_or_paths = _collect_names_paths(root)
        assert any("app.py" in (n or "") for n in names_or_paths)
        assert any("utils.py" in (n or "") for n in names_or_paths)

    def test_methods_roll_up_into_container_members(self, tmp_db) -> None:
        """Methods (MyClass.do_thing) appear as members count on the container, not as nodes."""
        from seam.query.structure import build_structure

        conn, tmp_path, _ = tmp_db
        _seed_basic(conn, tmp_path)

        result = build_structure(conn, tmp_path)
        # Find the MyClass container node
        my_class_node = _find_node_by_name(result["tree"], "MyClass")
        assert my_class_node is not None, "MyClass container node not found"
        assert my_class_node["kind"] == "container"
        # members count should include MyClass.do_thing
        assert my_class_node["members"] >= 1

    def test_method_not_a_separate_top_level_node(self, tmp_db) -> None:
        """MyClass.do_thing must NOT appear as a separate file-level node."""
        from seam.query.structure import build_structure

        conn, tmp_path, _ = tmp_db
        _seed_basic(conn, tmp_path)

        result = build_structure(conn, tmp_path)
        # Traverse only file-level children (not container children)
        file_node = _find_node_by_path_fragment(result["tree"], "app.py")
        assert file_node is not None
        child_names = [c["name"] for c in file_node.get("children", [])]
        assert "MyClass.do_thing" not in child_names

    def test_top_level_function_present_under_file(self, tmp_db) -> None:
        """standalone_func and top_level appear under their respective file nodes."""
        from seam.query.structure import build_structure

        conn, tmp_path, _ = tmp_db
        _seed_basic(conn, tmp_path)

        result = build_structure(conn, tmp_path)
        file_node = _find_node_by_path_fragment(result["tree"], "app.py")
        assert file_node is not None
        child_names = [c["name"] for c in file_node.get("children", [])]
        assert "standalone_func" in child_names

    def test_container_path_is_none(self, tmp_db) -> None:
        """Container nodes have path=None (they are logical, not file-backed)."""
        from seam.query.structure import build_structure

        conn, tmp_path, _ = tmp_db
        _seed_basic(conn, tmp_path)

        result = build_structure(conn, tmp_path)
        my_class_node = _find_node_by_name(result["tree"], "MyClass")
        assert my_class_node is not None
        assert my_class_node["path"] is None

    def test_file_node_path_is_relative(self, tmp_db) -> None:
        """File nodes carry a path relative to root, not an absolute path."""
        from seam.query.structure import build_structure

        conn, tmp_path, _ = tmp_db
        _seed_basic(conn, tmp_path)

        result = build_structure(conn, tmp_path)
        file_node = _find_node_by_path_fragment(result["tree"], "app.py")
        assert file_node is not None
        assert not Path(file_node["path"]).is_absolute(), (
            f"Expected relative path, got absolute: {file_node['path']}"
        )

    def test_subdir_appears_as_nested_dir(self, tmp_db) -> None:
        """subdir/ appears as a nested 'dir' node containing helper.py."""
        from seam.query.structure import build_structure

        conn, tmp_path, _ = tmp_db
        _seed_basic(conn, tmp_path)

        result = build_structure(conn, tmp_path)
        subdir_node = _find_node_by_name(result["tree"], "subdir")
        assert subdir_node is not None
        assert subdir_node["kind"] == "dir"
        # It should contain helper.py
        child_names = [c["name"] for c in subdir_node.get("children", [])]
        assert any("helper.py" in n for n in child_names)

    def test_symbol_count_on_file_node(self, tmp_db) -> None:
        """symbol_count on file node reflects total symbols in that file."""
        from seam.query.structure import build_structure

        conn, tmp_path, _ = tmp_db
        _seed_basic(conn, tmp_path)

        result = build_structure(conn, tmp_path)
        file_node = _find_node_by_path_fragment(result["tree"], "app.py")
        assert file_node is not None
        # app.py has 3 symbols: MyClass, MyClass.do_thing, standalone_func
        assert file_node["symbol_count"] == 3

    def test_empty_index_returns_safe_tree(self, tmp_db) -> None:
        """An empty index returns a valid tree (not an error, not a raise)."""
        from seam.query.structure import build_structure

        conn, tmp_path, _ = tmp_db
        # No seeding — empty index
        result = build_structure(conn, tmp_path)
        assert "tree" in result
        assert result["truncated"] == 0

    def test_never_raises_on_garbage_db(self) -> None:
        """build_structure never raises even on a partial/empty DB."""
        from seam.query.structure import build_structure

        # Use in-memory DB without full schema — just check no exception escapes
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE IF NOT EXISTS files (id INTEGER PRIMARY KEY, path TEXT)")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS symbols (id INTEGER PRIMARY KEY, file_id INTEGER, name TEXT, kind TEXT, start_line INTEGER)"
        )
        conn.commit()
        try:
            result = build_structure(conn, Path("/tmp"))
        except Exception as exc:
            pytest.fail(f"build_structure raised: {exc}")
        finally:
            conn.close()
        assert "tree" in result


# ── S2: Handler ────────────────────────────────────────────────────────────────


class TestHandleSeamStructure:
    """S2: handle_seam_structure relativizes paths and returns StructureResult shape."""

    def test_handler_returns_tree_and_truncated(self, tmp_db) -> None:
        """handle_seam_structure returns a dict with tree + truncated."""
        from seam.server.tools import handle_seam_structure

        conn, tmp_path, _ = tmp_db
        _seed_basic(conn, tmp_path)

        result = handle_seam_structure(conn, tmp_path)
        assert "tree" in result
        assert "truncated" in result

    def test_no_absolute_paths_in_output(self, tmp_db) -> None:
        """No absolute paths leak into the handler output."""
        from seam.server.tools import handle_seam_structure

        conn, tmp_path, _ = tmp_db
        _seed_basic(conn, tmp_path)

        result = handle_seam_structure(conn, tmp_path)
        all_paths = _collect_all_paths(result["tree"])
        for p in all_paths:
            if p is not None:
                assert not Path(p).is_absolute(), f"Absolute path leaked: {p}"

    def test_tool_count_is_twelve(self, tmp_db) -> None:
        """seam_structure is registered and the total tool count is 12."""
        from seam.server.mcp import create_server

        conn, tmp_path, _ = tmp_db

        server = create_server(conn, tmp_path)
        conn.close()

        tool_names = list(server._tool_manager._tools.keys())
        assert "seam_structure" in tool_names, f"seam_structure not in tools: {sorted(tool_names)}"
        assert len(tool_names) == 12, (
            f"Expected 12 tools, got {len(tool_names)}: {sorted(tool_names)}"
        )


# ── S3: CLI ────────────────────────────────────────────────────────────────────


class TestCLIStructure:
    """S3: `seam structure` CLI command — --json envelope and NO_INDEX guard."""

    def test_json_envelope_shape(self, tmp_db) -> None:
        """seam structure --json emits {ok: true, data: {tree: ..., truncated: ...}}."""
        from seam.cli.main import app

        conn, tmp_path, db_path = tmp_db
        _seed_basic(conn, tmp_path)
        conn.close()  # CLI opens its own connection

        runner = CliRunner()
        result = runner.invoke(app, ["structure", str(tmp_path), "--json"])
        assert result.exit_code == 0, f"CLI failed: {result.output}"
        data = json.loads(result.output)
        assert data["ok"] is True
        assert "tree" in data["data"]
        assert "truncated" in data["data"]

    def test_no_index_error_json(self, tmp_path: Path) -> None:
        """seam structure --json returns NO_INDEX envelope when index is missing."""
        from seam.cli.main import app

        runner = CliRunner()
        result = runner.invoke(app, ["structure", str(tmp_path), "--json"])
        assert result.exit_code != 0
        data = json.loads(result.output)
        assert data["ok"] is False
        assert data["error"]["code"] == "NO_INDEX"

    def test_quiet_mode_produces_output(self, tmp_db) -> None:
        """seam structure --quiet renders without error and produces some output."""
        from seam.cli.main import app

        conn, tmp_path, db_path = tmp_db
        _seed_basic(conn, tmp_path)
        conn.close()

        runner = CliRunner()
        result = runner.invoke(app, ["structure", str(tmp_path), "--quiet"])
        assert result.exit_code == 0, f"CLI failed: {result.output}"
        # Quiet mode should produce some output (tree rendering)
        assert result.output.strip() != ""


# ── Tree-walking helpers ───────────────────────────────────────────────────────


def _collect_names_paths(node: dict) -> list[str | None]:
    """Collect all name and path strings recursively."""
    result = [node.get("name"), node.get("path")]
    for child in node.get("children", []):
        result.extend(_collect_names_paths(child))
    return result


def _find_node_by_name(node: dict, name: str) -> dict | None:
    """DFS search for a node with the given name."""
    if node.get("name") == name:
        return node
    for child in node.get("children", []):
        found = _find_node_by_name(child, name)
        if found is not None:
            return found
    return None


def _find_node_by_path_fragment(node: dict, fragment: str) -> dict | None:
    """DFS search for a node whose path contains the given fragment."""
    path = node.get("path") or ""
    if fragment in path:
        return node
    for child in node.get("children", []):
        found = _find_node_by_path_fragment(child, fragment)
        if found is not None:
            return found
    return None


def _collect_all_paths(node: dict) -> list[str | None]:
    """Collect all 'path' values recursively."""
    result = [node.get("path")]
    for child in node.get("children", []):
        result.extend(_collect_all_paths(child))
    return result
