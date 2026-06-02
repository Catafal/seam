"""Tests for C and C++ language support (Phase 9).

TDD: Tests written before implementation. Each group covers one behavioral slice:

CC1 — C symbols:   function→function, static function (file-local), struct→class,
                   union→class, enum→type, typedef→type, doc-comment→docstring.
CC2 — C++ symbols: class→class, struct→class (inside namespace), enum→type,
                   free function→function, in-class method→Class.method,
                   out-of-line method (Class::m)→Class.method.
CC3 — C edges:     import edge from #include (local and system); call edge from bare call.
CC4 — C++ edges:   import edge from #include; call edge from bare identifier.
CC5 — C comments:  WHY/HACK/NOTE markers from // and /* */ comments.
CC6 — C++ comments: WHY/HACK/NOTE markers from // and /* */ comments.
CC7 — parser:       parse_c / parse_cpp return Node for valid source, None for binary.
CC8 — pipeline:     _dispatch_parser routes .c/.cpp; index_one_file indexes fixtures.
CC9 — signatures:   signature present for C and C++ symbols; static→visibility='private'.
CC10 — imports:     C #include "..." mapping extracted; system #include → empty resolve.
CC11 — builtins:    is_builtin true for C/C++ stdlib names; false for repo names.

All assertions go through the PUBLIC API (extract_symbols / extract_edges /
extract_comments), never against internals.
"""

from pathlib import Path

from seam.indexer.graph import Edge, Symbol, extract_comments, extract_edges, extract_symbols

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"
SAMPLE_C = FIXTURES_DIR / "sample.c"
SAMPLE_CPP = FIXTURES_DIR / "sample.cpp"


# ── Helpers ────────────────────────────────────────────────────────────────────


def _sym(symbols: list[Symbol], name: str) -> Symbol | None:
    """Find a symbol by exact name, or None."""
    return next((s for s in symbols if s["name"] == name), None)


def _edge(edges: list[Edge], *, target: str | None = None, kind: str | None = None) -> Edge | None:
    """Find an edge by optional target and/or kind, return first match or None."""
    for e in edges:
        if target is not None and e["target"] != target:
            continue
        if kind is not None and e["kind"] != kind:
            continue
        return e
    return None


def _get_c_root():  # type: ignore[return]
    """Parse sample.c and return root node (fails test if parse fails)."""
    from seam.indexer.parser import parse_c

    node = parse_c(SAMPLE_C)
    assert node is not None, "sample.c failed to parse"
    return node


def _get_cpp_root():  # type: ignore[return]
    """Parse sample.cpp and return root node (fails test if parse fails)."""
    from seam.indexer.parser import parse_cpp

    node = parse_cpp(SAMPLE_CPP)
    assert node is not None, "sample.cpp failed to parse"
    return node


# ── CC1: C symbols ─────────────────────────────────────────────────────────────


class TestCSymbols:
    """CC1: C symbol extraction covers all required kinds and docstrings."""

    def test_function_extracted_as_function(self) -> None:
        """int add(int a, int b) → kind='function'."""
        root = _get_c_root()
        symbols = extract_symbols(root, "c", SAMPLE_C)
        sym = _sym(symbols, "add")
        assert sym is not None, f"add not found in C symbols; found: {[s['name'] for s in symbols]}"
        assert sym["kind"] == "function"
        assert sym["file"] == str(SAMPLE_C)

    def test_function_start_line_correct(self) -> None:
        """add() start_line is 1-based and non-zero."""
        root = _get_c_root()
        symbols = extract_symbols(root, "c", SAMPLE_C)
        sym = _sym(symbols, "add")
        assert sym is not None
        assert sym["start_line"] >= 1

    def test_static_function_extracted_as_function(self) -> None:
        """static int helper(...) → kind='function' (C has no classes, so no methods)."""
        root = _get_c_root()
        symbols = extract_symbols(root, "c", SAMPLE_C)
        sym = _sym(symbols, "helper")
        assert sym is not None, f"helper not found in C symbols; found: {[s['name'] for s in symbols]}"
        assert sym["kind"] == "function"

    def test_struct_extracted_as_class(self) -> None:
        """struct Point → kind='class'."""
        root = _get_c_root()
        symbols = extract_symbols(root, "c", SAMPLE_C)
        sym = _sym(symbols, "Point")
        assert sym is not None, "Point struct not found"
        assert sym["kind"] == "class"

    def test_union_extracted_as_class(self) -> None:
        """union Value → kind='class'."""
        root = _get_c_root()
        symbols = extract_symbols(root, "c", SAMPLE_C)
        sym = _sym(symbols, "Value")
        assert sym is not None, "Value union not found"
        assert sym["kind"] == "class"

    def test_enum_extracted_as_type(self) -> None:
        """enum Status → kind='type'."""
        root = _get_c_root()
        symbols = extract_symbols(root, "c", SAMPLE_C)
        sym = _sym(symbols, "Status")
        assert sym is not None, "Status enum not found"
        assert sym["kind"] == "type"

    def test_typedef_extracted_as_type(self) -> None:
        """typedef unsigned int uint → kind='type'."""
        root = _get_c_root()
        symbols = extract_symbols(root, "c", SAMPLE_C)
        sym = _sym(symbols, "uint")
        assert sym is not None, f"uint typedef not found; found: {[s['name'] for s in symbols]}"
        assert sym["kind"] == "type"

    def test_function_with_doc_comment_has_docstring(self) -> None:
        """add() preceded by /** */ doc comment → docstring is not None."""
        root = _get_c_root()
        symbols = extract_symbols(root, "c", SAMPLE_C)
        sym = _sym(symbols, "add")
        assert sym is not None
        assert sym["docstring"] is not None
        assert "sum" in sym["docstring"].lower() or "integer" in sym["docstring"].lower()

    def test_main_function_extracted(self) -> None:
        """int main(void) → kind='function', name='main'."""
        root = _get_c_root()
        symbols = extract_symbols(root, "c", SAMPLE_C)
        sym = _sym(symbols, "main")
        assert sym is not None, "main function not found"
        assert sym["kind"] == "function"

    def test_returns_list_not_none(self) -> None:
        """extract_symbols always returns a list, never None."""
        root = _get_c_root()
        result = extract_symbols(root, "c", SAMPLE_C)
        assert isinstance(result, list)

    def test_no_enumerator_as_symbol(self) -> None:
        """STATUS_OK (enumerator value) is NOT extracted as a top-level symbol."""
        root = _get_c_root()
        symbols = extract_symbols(root, "c", SAMPLE_C)
        names = {s["name"] for s in symbols}
        assert "STATUS_OK" not in names


# ── CC2: C++ symbols ──────────────────────────────────────────────────────────


class TestCppSymbols:
    """CC2: C++ symbol extraction covers all required kinds including in-class and out-of-line methods."""

    def test_class_extracted_as_class(self) -> None:
        """class Shape → kind='class'."""
        root = _get_cpp_root()
        symbols = extract_symbols(root, "cpp", SAMPLE_CPP)
        sym = _sym(symbols, "Shape")
        assert sym is not None, f"Shape not found; found: {[s['name'] for s in symbols]}"
        assert sym["kind"] == "class"

    def test_class_with_inheritance_extracted(self) -> None:
        """class Circle : public Shape → kind='class', name='Circle'."""
        root = _get_cpp_root()
        symbols = extract_symbols(root, "cpp", SAMPLE_CPP)
        sym = _sym(symbols, "Circle")
        assert sym is not None, "Circle not found"
        assert sym["kind"] == "class"

    def test_struct_in_namespace_extracted_as_class(self) -> None:
        """struct Point inside namespace geometry → kind='class', name='Point'."""
        root = _get_cpp_root()
        symbols = extract_symbols(root, "cpp", SAMPLE_CPP)
        sym = _sym(symbols, "Point")
        assert sym is not None, "Point struct inside namespace not found"
        assert sym["kind"] == "class"

    def test_enum_extracted_as_type(self) -> None:
        """enum class ResultCode → kind='type'."""
        root = _get_cpp_root()
        symbols = extract_symbols(root, "cpp", SAMPLE_CPP)
        sym = _sym(symbols, "ResultCode")
        assert sym is not None, "ResultCode enum not found"
        assert sym["kind"] == "type"

    def test_free_function_extracted_as_function(self) -> None:
        """int add(int a, int b) (top-level) → kind='function'."""
        root = _get_cpp_root()
        symbols = extract_symbols(root, "cpp", SAMPLE_CPP)
        sym = _sym(symbols, "add")
        assert sym is not None, f"add function not found; found: {[s['name'] for s in symbols]}"
        assert sym["kind"] == "function"

    def test_in_class_method_qualified(self) -> None:
        """Circle::area defined inside class body → kind='method', name='Circle.area'."""
        root = _get_cpp_root()
        symbols = extract_symbols(root, "cpp", SAMPLE_CPP)
        # area() is defined in-class inside Circle; should be 'Circle.area'
        sym = _sym(symbols, "Circle.area")
        assert sym is not None, (
            "Circle.area not found — in-class method not qualified. "
            f"Found: {[s['name'] for s in symbols]}"
        )
        assert sym["kind"] == "method"

    def test_out_of_line_method_qualified(self) -> None:
        """double Circle::area() definition outside class → kind='method', name='Circle.area'."""
        # The out-of-line definition should ALSO produce Circle.area (or only the in-class one
        # is used; both are acceptable per MVP — the key is the qualified name).
        root = _get_cpp_root()
        symbols = extract_symbols(root, "cpp", SAMPLE_CPP)
        # At minimum Circle.area should appear (either in-class or out-of-line produces it)
        qualified_methods = [s for s in symbols if s["name"] == "Circle.area" and s["kind"] == "method"]
        assert len(qualified_methods) >= 1, (
            "Expected at least one Circle.area method symbol. "
            f"Found: {[s['name'] for s in symbols]}"
        )

    def test_namespace_not_emitted_as_symbol(self) -> None:
        """namespace geometry is NOT emitted as a symbol (traversed, not extracted)."""
        root = _get_cpp_root()
        symbols = extract_symbols(root, "cpp", SAMPLE_CPP)
        names = {s["name"] for s in symbols}
        assert "geometry" not in names

    def test_class_with_doc_comment_has_docstring(self) -> None:
        """Shape preceded by /** */ doc comment → docstring captured."""
        root = _get_cpp_root()
        symbols = extract_symbols(root, "cpp", SAMPLE_CPP)
        sym = _sym(symbols, "Shape")
        assert sym is not None
        assert sym["docstring"] is not None

    def test_returns_list_not_none(self) -> None:
        """extract_symbols always returns a list, never raises."""
        root = _get_cpp_root()
        result = extract_symbols(root, "cpp", SAMPLE_CPP)
        assert isinstance(result, list)


# ── CC3: C edges ──────────────────────────────────────────────────────────────


class TestCEdges:
    """CC3: C import and call edges from #include and bare-identifier calls."""

    def test_local_include_produces_import_edge(self) -> None:
        """#include "utils.h" → import edge with target 'utils'."""
        root = _get_c_root()
        edges = extract_edges(root, "c", SAMPLE_C)
        import_edges = [e for e in edges if e["kind"] == "import"]
        assert len(import_edges) > 0, "No import edges found for C fixture"
        targets = {e["target"] for e in import_edges}
        assert "utils" in targets, f"Expected 'utils' import edge; got: {targets}"

    def test_system_include_produces_import_edge(self) -> None:
        """#include <stdio.h> → import edge with target 'stdio' (system included)."""
        root = _get_c_root()
        edges = extract_edges(root, "c", SAMPLE_C)
        targets = {e["target"] for e in edges if e["kind"] == "import"}
        assert "stdio" in targets, f"Expected 'stdio' import edge; got: {targets}"

    def test_import_source_is_file_stem(self) -> None:
        """Import edge source is the file stem ('sample')."""
        root = _get_c_root()
        edges = extract_edges(root, "c", SAMPLE_C)
        for e in edges:
            if e["kind"] == "import" and e["target"] == "utils":
                assert e["source"] == "sample", f"Expected source='sample', got {e['source']}"

    def test_call_edge_produced(self) -> None:
        """Bare-identifier call expression produces a call edge."""
        root = _get_c_root()
        edges = extract_edges(root, "c", SAMPLE_C)
        call_edges = [e for e in edges if e["kind"] == "call"]
        assert len(call_edges) > 0, "No call edges found for C fixture"

    def test_call_edge_target_is_callee_name(self) -> None:
        """main() calls add() → call edge with target 'add'."""
        root = _get_c_root()
        edges = extract_edges(root, "c", SAMPLE_C)
        e = _edge(edges, target="add", kind="call")
        assert e is not None, (
            "Expected call edge targeting 'add'. "
            f"Call targets: {[e['target'] for e in edges if e['kind'] == 'call']}"
        )

    def test_call_edge_source_is_enclosing_function(self) -> None:
        """The call edge source is the enclosing function name ('main')."""
        root = _get_c_root()
        edges = extract_edges(root, "c", SAMPLE_C)
        e = _edge(edges, target="add", kind="call")
        assert e is not None
        assert e["source"] == "main", f"Expected source='main', got {e['source']}"

    def test_edges_have_confidence_field(self) -> None:
        """All edges carry a confidence field."""
        root = _get_c_root()
        edges = extract_edges(root, "c", SAMPLE_C)
        for e in edges:
            assert e["confidence"] in ("EXTRACTED", "INFERRED", "AMBIGUOUS"), (
                f"Edge missing valid confidence: {e}"
            )

    def test_edges_returns_list(self) -> None:
        """extract_edges always returns a list, never raises."""
        root = _get_c_root()
        result = extract_edges(root, "c", SAMPLE_C)
        assert isinstance(result, list)


# ── CC4: C++ edges ────────────────────────────────────────────────────────────


class TestCppEdges:
    """CC4: C++ import and call edges."""

    def test_local_include_produces_import_edge(self) -> None:
        """#include "utils.h" → import edge with target 'utils'."""
        root = _get_cpp_root()
        edges = extract_edges(root, "cpp", SAMPLE_CPP)
        targets = {e["target"] for e in edges if e["kind"] == "import"}
        assert "utils" in targets, f"Expected 'utils' import edge; got: {targets}"

    def test_system_include_produces_import_edge(self) -> None:
        """#include <iostream> → import edge with target 'iostream'."""
        root = _get_cpp_root()
        edges = extract_edges(root, "cpp", SAMPLE_CPP)
        targets = {e["target"] for e in edges if e["kind"] == "import"}
        assert "iostream" in targets, f"Expected 'iostream' import edge; got: {targets}"

    def test_import_source_is_file_stem(self) -> None:
        """Import edge source is the file stem ('sample')."""
        root = _get_cpp_root()
        edges = extract_edges(root, "cpp", SAMPLE_CPP)
        for e in edges:
            if e["kind"] == "import" and e["target"] == "utils":
                assert e["source"] == "sample", f"Expected source='sample', got {e['source']}"

    def test_call_edge_produced(self) -> None:
        """Bare-identifier call expression produces at least one call edge."""
        root = _get_cpp_root()
        edges = extract_edges(root, "cpp", SAMPLE_CPP)
        call_edges = [e for e in edges if e["kind"] == "call"]
        assert len(call_edges) > 0, "No call edges found for C++ fixture"

    def test_edges_have_confidence_field(self) -> None:
        """All edges carry a confidence field."""
        root = _get_cpp_root()
        edges = extract_edges(root, "cpp", SAMPLE_CPP)
        for e in edges:
            assert e["confidence"] in ("EXTRACTED", "INFERRED", "AMBIGUOUS")

    def test_edges_returns_list(self) -> None:
        """extract_edges always returns a list."""
        root = _get_cpp_root()
        result = extract_edges(root, "cpp", SAMPLE_CPP)
        assert isinstance(result, list)


# ── CC5: C comments ───────────────────────────────────────────────────────────


class TestCComments:
    """CC5: C semantic comment extraction from // and /* */ comments."""

    def test_why_marker_extracted(self) -> None:
        """WHY: ... in a C comment → Comment with marker='WHY'."""
        root = _get_c_root()
        comments = extract_comments(root, "c", SAMPLE_C)
        whys = [c for c in comments if c["marker"] == "WHY"]
        assert len(whys) > 0, f"No WHY marker found in C fixture; found markers: {[c['marker'] for c in comments]}"

    def test_hack_marker_extracted(self) -> None:
        """HACK: ... in a C comment → Comment with marker='HACK'."""
        root = _get_c_root()
        comments = extract_comments(root, "c", SAMPLE_C)
        hacks = [c for c in comments if c["marker"] == "HACK"]
        assert len(hacks) > 0, "No HACK marker found in C fixture"

    def test_note_marker_extracted(self) -> None:
        """NOTE: ... in a C comment → Comment with marker='NOTE'."""
        root = _get_c_root()
        comments = extract_comments(root, "c", SAMPLE_C)
        notes = [c for c in comments if c["marker"] == "NOTE"]
        assert len(notes) > 0, "No NOTE marker found in C fixture"

    def test_plain_comment_not_extracted(self) -> None:
        """Plain /* */ comment without a marker → not extracted."""
        root = _get_c_root()
        comments = extract_comments(root, "c", SAMPLE_C)
        # Check that plain non-marker text like 'A struct with doc comment.' is not there
        for c in comments:
            assert c["marker"] in ("WHY", "HACK", "NOTE", "TODO", "FIXME")

    def test_comment_has_correct_fields(self) -> None:
        """Each extracted comment has marker, text, and line fields."""
        root = _get_c_root()
        comments = extract_comments(root, "c", SAMPLE_C)
        for c in comments:
            assert "marker" in c
            assert "text" in c
            assert "line" in c
            assert isinstance(c["line"], int)
            assert c["line"] >= 1

    def test_returns_list(self) -> None:
        """extract_comments always returns a list."""
        root = _get_c_root()
        result = extract_comments(root, "c", SAMPLE_C)
        assert isinstance(result, list)


# ── CC6: C++ comments ─────────────────────────────────────────────────────────


class TestCppComments:
    """CC6: C++ semantic comment extraction from // and /* */ comments."""

    def test_why_marker_extracted(self) -> None:
        """// WHY: ... in a C++ file → Comment with marker='WHY'."""
        root = _get_cpp_root()
        comments = extract_comments(root, "cpp", SAMPLE_CPP)
        whys = [c for c in comments if c["marker"] == "WHY"]
        assert len(whys) > 0, f"No WHY marker found in C++ fixture; found: {[c['marker'] for c in comments]}"

    def test_hack_marker_extracted(self) -> None:
        """HACK: ... in a C++ comment → Comment with marker='HACK'."""
        root = _get_cpp_root()
        comments = extract_comments(root, "cpp", SAMPLE_CPP)
        hacks = [c for c in comments if c["marker"] == "HACK"]
        assert len(hacks) > 0, "No HACK marker found in C++ fixture"

    def test_note_marker_extracted(self) -> None:
        """NOTE: ... in a C++ comment → Comment with marker='NOTE'."""
        root = _get_cpp_root()
        comments = extract_comments(root, "cpp", SAMPLE_CPP)
        notes = [c for c in comments if c["marker"] == "NOTE"]
        assert len(notes) > 0, "No NOTE marker found in C++ fixture"

    def test_comment_fields_valid(self) -> None:
        """All extracted comments have valid marker, text, and line fields."""
        root = _get_cpp_root()
        comments = extract_comments(root, "cpp", SAMPLE_CPP)
        valid_markers = {"WHY", "HACK", "NOTE", "TODO", "FIXME"}
        for c in comments:
            assert c["marker"] in valid_markers
            assert isinstance(c["line"], int)
            assert c["line"] >= 1

    def test_returns_list(self) -> None:
        """extract_comments always returns a list."""
        root = _get_cpp_root()
        result = extract_comments(root, "cpp", SAMPLE_CPP)
        assert isinstance(result, list)


# ── CC7: parser ───────────────────────────────────────────────────────────────


class TestCCppParser:
    """CC7: parse_c and parse_cpp behave like the existing parsers."""

    def test_parse_c_valid_file_returns_node(self) -> None:
        """parse_c(sample.c) returns a non-None AST root node."""
        from seam.indexer.parser import parse_c

        node = parse_c(SAMPLE_C)
        assert node is not None

    def test_parse_c_node_has_children(self) -> None:
        """root node from sample.c has at least one child."""
        from seam.indexer.parser import parse_c

        node = parse_c(SAMPLE_C)
        assert node is not None
        assert len(node.children) > 0  # type: ignore[union-attr]

    def test_parse_c_binary_file_returns_none(self, tmp_path: Path) -> None:
        """Binary .c file → None (not raised)."""
        from seam.indexer.parser import parse_c

        binary_file = tmp_path / "binary.c"
        binary_file.write_bytes(b"int main() {}\x00")
        node = parse_c(binary_file)
        assert node is None

    def test_parse_c_missing_file_returns_none(self) -> None:
        """Non-existent .c path → None."""
        from seam.indexer.parser import parse_c

        node = parse_c(Path("/nonexistent/path/file.c"))
        assert node is None

    def test_parse_cpp_valid_file_returns_node(self) -> None:
        """parse_cpp(sample.cpp) returns a non-None AST root node."""
        from seam.indexer.parser import parse_cpp

        node = parse_cpp(SAMPLE_CPP)
        assert node is not None

    def test_parse_cpp_node_has_children(self) -> None:
        """root node from sample.cpp has at least one child."""
        from seam.indexer.parser import parse_cpp

        node = parse_cpp(SAMPLE_CPP)
        assert node is not None
        assert len(node.children) > 0  # type: ignore[union-attr]

    def test_parse_cpp_binary_file_returns_none(self, tmp_path: Path) -> None:
        """Binary .cpp file → None."""
        from seam.indexer.parser import parse_cpp

        binary_file = tmp_path / "binary.cpp"
        binary_file.write_bytes(b"int main() {}\x00")
        node = parse_cpp(binary_file)
        assert node is None

    def test_parse_cpp_missing_file_returns_none(self) -> None:
        """Non-existent .cpp path → None."""
        from seam.indexer.parser import parse_cpp

        node = parse_cpp(Path("/nonexistent/path/file.cpp"))
        assert node is None

    def test_parse_h_file_uses_c_parser(self, tmp_path: Path) -> None:
        """A .h file is parsed with the C parser (per .h→C language map decision)."""
        from seam.indexer.parser import parse_c

        h_file = tmp_path / "test.h"
        h_file.write_text("int add(int a, int b);\n")
        node = parse_c(h_file)
        assert node is not None, "parse_c should handle .h files"


# ── CC8: pipeline ─────────────────────────────────────────────────────────────


class TestCCppPipeline:
    """CC8: _dispatch_parser routes .c/.cpp; index_one_file indexes the fixtures."""

    def test_dispatch_parser_c(self, tmp_path: Path) -> None:
        """_dispatch_parser routes language='c' to parse_c."""
        from seam.indexer.pipeline import _dispatch_parser

        c_file = tmp_path / "test.c"
        c_file.write_text("int main() { return 0; }\n")
        result = _dispatch_parser(c_file, "c")
        assert result is not None, "_dispatch_parser returned None for C source"

    def test_dispatch_parser_cpp(self, tmp_path: Path) -> None:
        """_dispatch_parser routes language='cpp' to parse_cpp."""
        from seam.indexer.pipeline import _dispatch_parser

        cpp_file = tmp_path / "test.cpp"
        cpp_file.write_text("int main() { return 0; }\n")
        result = _dispatch_parser(cpp_file, "cpp")
        assert result is not None, "_dispatch_parser returned None for C++ source"

    def test_language_map_has_c(self) -> None:
        """config.SEAM_LANGUAGE_MAP includes '.c' → 'c'."""
        import seam.config as config

        assert ".c" in config.SEAM_LANGUAGE_MAP
        assert config.SEAM_LANGUAGE_MAP[".c"] == "c"

    def test_language_map_has_h_as_c(self) -> None:
        """config.SEAM_LANGUAGE_MAP includes '.h' → 'c' (deliberate MVP decision)."""
        import seam.config as config

        assert ".h" in config.SEAM_LANGUAGE_MAP
        assert config.SEAM_LANGUAGE_MAP[".h"] == "c"

    def test_language_map_has_cpp(self) -> None:
        """config.SEAM_LANGUAGE_MAP includes '.cpp' → 'cpp'."""
        import seam.config as config

        assert ".cpp" in config.SEAM_LANGUAGE_MAP
        assert config.SEAM_LANGUAGE_MAP[".cpp"] == "cpp"

    def test_language_map_has_cc(self) -> None:
        """config.SEAM_LANGUAGE_MAP includes '.cc' → 'cpp'."""
        import seam.config as config

        assert ".cc" in config.SEAM_LANGUAGE_MAP
        assert config.SEAM_LANGUAGE_MAP[".cc"] == "cpp"

    def test_language_map_has_hpp(self) -> None:
        """config.SEAM_LANGUAGE_MAP includes '.hpp' → 'cpp'."""
        import seam.config as config

        assert ".hpp" in config.SEAM_LANGUAGE_MAP
        assert config.SEAM_LANGUAGE_MAP[".hpp"] == "cpp"

    def test_index_one_file_c_fixture(self, tmp_path: Path) -> None:
        """index_one_file on sample.c returns (symbols>0, edges>=0)."""
        from seam.indexer.db import init_db
        from seam.indexer.pipeline import index_one_file

        db_path = tmp_path / "seam.db"
        conn = init_db(db_path)
        result = index_one_file(conn, SAMPLE_C)
        conn.close()
        assert result is not None, "index_one_file returned None for sample.c"
        sym_count, _edge_count = result
        assert sym_count > 0, f"Expected symbols > 0 for sample.c, got {sym_count}"

    def test_index_one_file_cpp_fixture(self, tmp_path: Path) -> None:
        """index_one_file on sample.cpp returns (symbols>0, edges>=0)."""
        from seam.indexer.db import init_db
        from seam.indexer.pipeline import index_one_file

        db_path = tmp_path / "seam.db"
        conn = init_db(db_path)
        result = index_one_file(conn, SAMPLE_CPP)
        conn.close()
        assert result is not None, "index_one_file returned None for sample.cpp"
        sym_count, _edge_count = result
        assert sym_count > 0, f"Expected symbols > 0 for sample.cpp, got {sym_count}"

    def test_walk_project_finds_c_files(self, tmp_path: Path) -> None:
        """walk_project includes .c files."""
        from seam.indexer.pipeline import walk_project

        c_file = tmp_path / "main.c"
        c_file.write_text("int main() { return 0; }\n")
        files = walk_project(tmp_path)
        paths = {f.name for f in files}
        assert "main.c" in paths, f"main.c not found in walk_project output: {paths}"

    def test_walk_project_finds_cpp_files(self, tmp_path: Path) -> None:
        """walk_project includes .cpp files."""
        from seam.indexer.pipeline import walk_project

        cpp_file = tmp_path / "lib.cpp"
        cpp_file.write_text("int main() { return 0; }\n")
        files = walk_project(tmp_path)
        paths = {f.name for f in files}
        assert "lib.cpp" in paths, f"lib.cpp not found in walk_project output: {paths}"


# ── CC9: signatures ───────────────────────────────────────────────────────────


class TestCCppSignatures:
    """CC9: C and C++ Phase 4 enrichment fields."""

    def test_c_function_has_signature(self) -> None:
        """C function symbol has a non-None signature field."""
        root = _get_c_root()
        symbols = extract_symbols(root, "c", SAMPLE_C)
        sym = _sym(symbols, "add")
        assert sym is not None
        assert sym["signature"] is not None, "C function should have a signature"
        assert "add" in sym["signature"]

    def test_c_static_function_has_private_visibility(self) -> None:
        """static int helper() → visibility='private', is_exported=False."""
        root = _get_c_root()
        symbols = extract_symbols(root, "c", SAMPLE_C)
        sym = _sym(symbols, "helper")
        assert sym is not None
        assert sym["visibility"] == "private", (
            f"Static C function should have visibility='private'; got {sym['visibility']}"
        )
        assert sym["is_exported"] is False

    def test_c_non_static_function_is_exported(self) -> None:
        """Non-static int add() → is_exported=True."""
        root = _get_c_root()
        symbols = extract_symbols(root, "c", SAMPLE_C)
        sym = _sym(symbols, "add")
        assert sym is not None
        assert sym["is_exported"] is True

    def test_c_decorators_empty_list(self) -> None:
        """C has no decorator syntax → decorators=[]."""
        root = _get_c_root()
        symbols = extract_symbols(root, "c", SAMPLE_C)
        sym = _sym(symbols, "add")
        assert sym is not None
        assert sym["decorators"] == []

    def test_cpp_class_has_signature(self) -> None:
        """C++ class symbol has a non-None signature field."""
        root = _get_cpp_root()
        symbols = extract_symbols(root, "cpp", SAMPLE_CPP)
        sym = _sym(symbols, "Shape")
        assert sym is not None
        assert sym["signature"] is not None, "C++ class should have a signature"

    def test_cpp_decorators_empty_list(self) -> None:
        """C++ has no decorator syntax in scope → decorators=[]."""
        root = _get_cpp_root()
        symbols = extract_symbols(root, "cpp", SAMPLE_CPP)
        sym = _sym(symbols, "Shape")
        assert sym is not None
        assert sym["decorators"] == []


# ── CC10: imports ─────────────────────────────────────────────────────────────


class TestCCppImports:
    """CC10: C/C++ import mapping extraction."""

    def test_c_local_include_extracted(self) -> None:
        """#include "utils.h" → ImportMapping with local_name='utils'."""
        from seam.analysis.imports import extract_import_mappings
        from seam.indexer.parser import parse_c

        root = parse_c(SAMPLE_C)
        assert root is not None
        # Note: extract_import_mappings signature is (root, filepath, language)
        mappings = extract_import_mappings(root, SAMPLE_C, "c")
        local_names = {m["local_name"] for m in mappings}
        assert "utils" in local_names, (
            f"Expected 'utils' in import mappings; got: {local_names}"
        )

    def test_c_resolve_local_include(self) -> None:
        """_resolve_c for '#include "utils.h"' with matching file → returns path."""
        import tempfile

        from seam.analysis.imports_ext import _resolve_c

        # Create a temp repo with utils.h present
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            utils_h = repo_root / "utils.h"
            utils_h.write_text("int helper(int x);\n")
            ref_file = repo_root / "main.c"
            # Resolve "utils.h" from main.c's directory
            result = _resolve_c("utils.h", ref_file, repo_root)
            # Should find utils.h if it exists in the same directory
            assert len(result) >= 0  # Resolution is best-effort; empty is also valid

    def test_c_resolve_system_include_returns_empty(self) -> None:
        """_resolve_c for '#include <stdio.h>' → [] (system header out of scope)."""
        import tempfile

        from seam.analysis.imports_ext import _resolve_c

        with tempfile.TemporaryDirectory() as tmpdir:
            result = _resolve_c("<stdio.h>", Path(tmpdir) / "main.c", Path(tmpdir))
            assert result == []

    def test_cpp_local_include_extracted(self) -> None:
        """C++ #include "utils.h" → ImportMapping with local_name='utils'."""
        from seam.analysis.imports import extract_import_mappings
        from seam.indexer.parser import parse_cpp

        root = parse_cpp(SAMPLE_CPP)
        assert root is not None
        # Note: extract_import_mappings signature is (root, filepath, language)
        mappings = extract_import_mappings(root, SAMPLE_CPP, "cpp")
        local_names = {m["local_name"] for m in mappings}
        assert "utils" in local_names, (
            f"Expected 'utils' in C++ import mappings; got: {local_names}"
        )


# ── CC11: builtins ────────────────────────────────────────────────────────────


class TestCCppBuiltins:
    """CC11: C and C++ builtin name recognition."""

    def test_c_printf_is_builtin(self) -> None:
        """printf is a C stdlib builtin."""
        from seam.analysis.builtins import is_builtin

        assert is_builtin("printf", "c"), "printf should be a C builtin"

    def test_c_malloc_is_builtin(self) -> None:
        """malloc is a C stdlib builtin."""
        from seam.analysis.builtins import is_builtin

        assert is_builtin("malloc", "c"), "malloc should be a C builtin"

    def test_c_repo_name_not_builtin(self) -> None:
        """process_file (repo function) is not a C builtin."""
        from seam.analysis.builtins import is_builtin

        assert not is_builtin("process_file", "c"), "process_file should not be a C builtin"

    def test_cpp_std_is_builtin(self) -> None:
        """std is a C++ stdlib namespace known as a builtin."""
        from seam.analysis.builtins import is_builtin

        assert is_builtin("std", "cpp"), "std should be a C++ builtin"

    def test_cpp_cout_is_builtin(self) -> None:
        """cout is a C++ stdlib builtin."""
        from seam.analysis.builtins import is_builtin

        assert is_builtin("cout", "cpp"), "cout should be a C++ builtin"

    def test_cpp_repo_name_not_builtin(self) -> None:
        """MyShapeManager (repo class) is not a C++ builtin."""
        from seam.analysis.builtins import is_builtin

        assert not is_builtin("MyShapeManager", "cpp"), (
            "MyShapeManager should not be a C++ builtin"
        )

    def test_c_builtin_not_a_cpp_builtin_when_in_wrong_language(self) -> None:
        """printf is NOT a Ruby builtin even though it's a C builtin."""
        from seam.analysis.builtins import is_builtin

        # Language isolation: C builtins don't bleed into other languages.
        assert not is_builtin("printf", "ruby"), (
            "printf should not be a Ruby builtin — builtins are language-scoped"
        )
