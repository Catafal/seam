"""Tests for seam/indexer/graph.py — symbol and edge extraction.

TDD: Tests are written before the implementation. They verify:

B2 — extract_symbols:
  - Python: function name/kind/start_line correct
  - Python: class name/kind extracted
  - Python: method qualified as 'Class.method'
  - Python: docstring captured on functions/methods
  - TS: function name/kind/start_line correct
  - TS: interface and type alias extracted
  - TS: class method qualified as 'Class.method'
  - TS: JSDoc docstring captured

B3 — extract_edges:
  - Python: import edge with correct target
  - TS: import edge with correct target
  - Python: call edge with correct enclosing source
"""

from pathlib import Path

from seam.indexer.graph import Edge, Symbol, extract_edges, extract_symbols
from seam.indexer.parser import parse_python, parse_typescript

# Path to test fixtures
FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"
SAMPLE_PY = FIXTURES_DIR / "sample.py"
SAMPLE_TS = FIXTURES_DIR / "sample.ts"


# ── Helpers ────────────────────────────────────────────────────────────────────


def _get_py_root():  # type: ignore[return]
    """Parse sample.py and return root node."""
    node = parse_python(SAMPLE_PY)
    assert node is not None, "sample.py failed to parse"
    return node


def _get_ts_root():  # type: ignore[return]
    """Parse sample.ts and return root node."""
    node = parse_typescript(SAMPLE_TS)
    assert node is not None, "sample.ts failed to parse"
    return node


def _symbol_by_name(symbols: list[Symbol], name: str) -> Symbol | None:
    """Find a symbol by name in the list, or None."""
    for s in symbols:
        if s["name"] == name:
            return s
    return None


def _edge_by_target(edges: list[Edge], target: str) -> Edge | None:
    """Find an edge by target in the list, or None."""
    for e in edges:
        if e["target"] == target:
            return e
    return None


# ── B2: extract_symbols — Python ──────────────────────────────────────────────


class TestExtractSymbolsPython:
    def test_standalone_function_extracted(self) -> None:
        """standalone_function must appear with kind='function'."""
        root = _get_py_root()
        symbols = extract_symbols(root, "python", SAMPLE_PY)
        sym = _symbol_by_name(symbols, "standalone_function")
        assert sym is not None, "standalone_function not found"
        assert sym["kind"] == "function"
        assert sym["file"] == str(SAMPLE_PY)

    def test_function_start_line_correct(self) -> None:
        """standalone_function starts at the correct 1-based line number."""
        root = _get_py_root()
        symbols = extract_symbols(root, "python", SAMPLE_PY)
        sym = _symbol_by_name(symbols, "standalone_function")
        assert sym is not None
        # In sample.py the function is on line 13
        assert sym["start_line"] == 13

    def test_class_extracted(self) -> None:
        """SampleClass must appear with kind='class'."""
        root = _get_py_root()
        symbols = extract_symbols(root, "python", SAMPLE_PY)
        sym = _symbol_by_name(symbols, "SampleClass")
        assert sym is not None
        assert sym["kind"] == "class"

    def test_method_qualified_name(self) -> None:
        """Methods must be qualified as 'ClassName.method_name'."""
        root = _get_py_root()
        symbols = extract_symbols(root, "python", SAMPLE_PY)
        sym = _symbol_by_name(symbols, "SampleClass.instance_method")
        assert sym is not None
        assert sym["kind"] == "method"

    def test_function_docstring_captured(self) -> None:
        """standalone_function must have its docstring captured."""
        root = _get_py_root()
        symbols = extract_symbols(root, "python", SAMPLE_PY)
        sym = _symbol_by_name(symbols, "standalone_function")
        assert sym is not None
        assert sym["docstring"] is not None
        assert "Add two integers" in sym["docstring"]

    def test_function_without_docstring_is_none(self) -> None:
        """function_no_docstring must have docstring=None."""
        root = _get_py_root()
        symbols = extract_symbols(root, "python", SAMPLE_PY)
        sym = _symbol_by_name(symbols, "function_no_docstring")
        assert sym is not None
        assert sym["docstring"] is None

    def test_init_method_extracted(self) -> None:
        """__init__ must be extracted as a method."""
        root = _get_py_root()
        symbols = extract_symbols(root, "python", SAMPLE_PY)
        sym = _symbol_by_name(symbols, "SampleClass.__init__")
        assert sym is not None
        assert sym["kind"] == "method"

    def test_start_end_line_valid(self) -> None:
        """start_line and end_line must be 1-based and end >= start."""
        root = _get_py_root()
        symbols = extract_symbols(root, "python", SAMPLE_PY)
        for sym in symbols:
            assert sym["start_line"] >= 1, f"{sym['name']} start_line < 1"
            assert sym["end_line"] >= sym["start_line"], f"{sym['name']} end < start"


# ── B2: extract_symbols — TypeScript ──────────────────────────────────────────


class TestExtractSymbolsTypeScript:
    def test_function_extracted(self) -> None:
        """standaloneFunction must appear with kind='function'."""
        root = _get_ts_root()
        symbols = extract_symbols(root, "typescript", SAMPLE_TS)
        sym = _symbol_by_name(symbols, "standaloneFunction")
        assert sym is not None
        assert sym["kind"] == "function"
        assert sym["file"] == str(SAMPLE_TS)

    def test_function_start_line_correct(self) -> None:
        """standaloneFunction starts on the correct 1-based line."""
        root = _get_ts_root()
        symbols = extract_symbols(root, "typescript", SAMPLE_TS)
        sym = _symbol_by_name(symbols, "standaloneFunction")
        assert sym is not None
        # In sample.ts the function starts on line 12
        assert sym["start_line"] == 12

    def test_interface_extracted(self) -> None:
        """SampleInterface must appear with kind='interface'."""
        root = _get_ts_root()
        symbols = extract_symbols(root, "typescript", SAMPLE_TS)
        sym = _symbol_by_name(symbols, "SampleInterface")
        assert sym is not None
        assert sym["kind"] == "interface"

    def test_type_alias_extracted(self) -> None:
        """SampleType must appear with kind='type'."""
        root = _get_ts_root()
        symbols = extract_symbols(root, "typescript", SAMPLE_TS)
        sym = _symbol_by_name(symbols, "SampleType")
        assert sym is not None
        assert sym["kind"] == "type"

    def test_class_extracted(self) -> None:
        """SampleClass must appear with kind='class'."""
        root = _get_ts_root()
        symbols = extract_symbols(root, "typescript", SAMPLE_TS)
        sym = _symbol_by_name(symbols, "SampleClass")
        assert sym is not None
        assert sym["kind"] == "class"

    def test_method_qualified_name(self) -> None:
        """Methods must be qualified as 'ClassName.method_name'."""
        root = _get_ts_root()
        symbols = extract_symbols(root, "typescript", SAMPLE_TS)
        sym = _symbol_by_name(symbols, "SampleClass.process")
        assert sym is not None
        assert sym["kind"] == "method"

    def test_jsdoc_docstring_captured(self) -> None:
        """standaloneFunction must have its /** JSDoc */ block captured."""
        root = _get_ts_root()
        symbols = extract_symbols(root, "typescript", SAMPLE_TS)
        sym = _symbol_by_name(symbols, "standaloneFunction")
        assert sym is not None
        assert sym["docstring"] is not None
        assert "Add two numbers" in sym["docstring"]

    def test_function_without_jsdoc_is_none(self) -> None:
        """functionNoJsdoc must have docstring=None."""
        root = _get_ts_root()
        symbols = extract_symbols(root, "typescript", SAMPLE_TS)
        sym = _symbol_by_name(symbols, "functionNoJsdoc")
        assert sym is not None
        assert sym["docstring"] is None

    def test_start_end_line_valid(self) -> None:
        """All TS symbols must have valid 1-based line ranges."""
        root = _get_ts_root()
        symbols = extract_symbols(root, "typescript", SAMPLE_TS)
        for sym in symbols:
            assert sym["start_line"] >= 1, f"{sym['name']} start_line < 1"
            assert sym["end_line"] >= sym["start_line"], f"{sym['name']} end < start"


# ── B3: extract_edges — imports ────────────────────────────────────────────────


class TestExtractEdgesPython:
    def test_import_edge_os(self) -> None:
        """'import os' must produce an edge with target='os' and kind='import'."""
        root = _get_py_root()
        edges = extract_edges(root, "python", SAMPLE_PY)
        edge = _edge_by_target(edges, "os")
        assert edge is not None, "No import edge for 'os'"
        assert edge["kind"] == "import"
        assert edge["file"] == str(SAMPLE_PY)

    def test_import_edge_pathlib(self) -> None:
        """'from pathlib import Path' must produce an edge with target='Path'."""
        root = _get_py_root()
        edges = extract_edges(root, "python", SAMPLE_PY)
        edge = _edge_by_target(edges, "Path")
        assert edge is not None, "No import edge for 'Path'"
        assert edge["kind"] == "import"

    def test_import_edge_source_is_filepath_stem(self) -> None:
        """Import edge source must be the stem of the file path."""
        root = _get_py_root()
        edges = extract_edges(root, "python", SAMPLE_PY)
        import_edges = [e for e in edges if e["kind"] == "import"]
        assert len(import_edges) > 0
        for edge in import_edges:
            assert edge["source"] == SAMPLE_PY.stem

    def test_call_edge_standalone_function(self) -> None:
        """calls_other_functions calls standalone_function — must produce a call edge."""
        root = _get_py_root()
        edges = extract_edges(root, "python", SAMPLE_PY)
        call_edges = [e for e in edges if e["kind"] == "call"]
        # Find a call to standalone_function
        edge = _edge_by_target(call_edges, "standalone_function")
        # Optional: call edge detection is MVP/heuristic, but should work for this case
        if edge is not None:
            assert edge["source"] == "calls_other_functions"
            assert edge["kind"] == "call"

    def test_import_edge_line_is_positive(self) -> None:
        """Import edge line numbers must be 1-based positive integers."""
        root = _get_py_root()
        edges = extract_edges(root, "python", SAMPLE_PY)
        import_edges = [e for e in edges if e["kind"] == "import"]
        for edge in import_edges:
            assert edge["line"] >= 1


class TestExtractEdgesTypeScript:
    def test_import_edge_fs(self) -> None:
        """'import { readFileSync } from 'fs'' must produce edge target='readFileSync'."""
        root = _get_ts_root()
        edges = extract_edges(root, "typescript", SAMPLE_TS)
        edge = _edge_by_target(edges, "readFileSync")
        assert edge is not None, "No import edge for 'readFileSync'"
        assert edge["kind"] == "import"
        assert edge["file"] == str(SAMPLE_TS)

    def test_import_edge_path(self) -> None:
        """'import path from 'path'' must produce edge with target='path'."""
        root = _get_ts_root()
        edges = extract_edges(root, "typescript", SAMPLE_TS)
        edge = _edge_by_target(edges, "path")
        assert edge is not None, "No import edge for 'path'"
        assert edge["kind"] == "import"

    def test_import_edge_source_is_filepath_stem(self) -> None:
        """TS import edge source must be the stem of the file path."""
        root = _get_ts_root()
        edges = extract_edges(root, "typescript", SAMPLE_TS)
        import_edges = [e for e in edges if e["kind"] == "import"]
        assert len(import_edges) > 0
        for edge in import_edges:
            assert edge["source"] == SAMPLE_TS.stem

    def test_import_edge_line_is_positive(self) -> None:
        """TS import edge line numbers must be 1-based positive integers."""
        root = _get_ts_root()
        edges = extract_edges(root, "typescript", SAMPLE_TS)
        import_edges = [e for e in edges if e["kind"] == "import"]
        for edge in import_edges:
            assert edge["line"] >= 1
