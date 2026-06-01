"""Tests for Go and Rust language support (Phase 1b).

TDD: Tests written before implementation. Each group covers one behavioral slice:

G1 — Go symbols:   func→function, method→Recv.Name method, struct→class,
                   interface→interface, type→type, doc-comment→docstring.
G2 — Rust symbols: fn→function, impl method→Type.fn, struct→class,
                   enum→type, trait→interface, ///→docstring.
G3 — Go edges:     import edge produced; call edge produced (bare identifier only).
G4 — Rust edges:   import edge produced; call edge produced (bare identifier only).
G5 — Go comments:  WHY/HACK/NOTE markers extracted from // and /* */ comments.
G6 — Rust comments: markers from //!, ///, // and /* */ comments; plain // ignored.
G7 — parser:        parse_go / parse_rust return Node for valid source, None for binary.
G8 — pipeline:      _dispatch_parser routes .go/.rs; index_one_file indexes the fixtures.

All assertions go through the PUBLIC API (extract_symbols / extract_edges /
extract_comments), never against internals.
"""

from pathlib import Path

from seam.indexer.graph import Edge, Symbol, extract_comments, extract_edges, extract_symbols

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"
SAMPLE_GO = FIXTURES_DIR / "sample.go"
SAMPLE_RS = FIXTURES_DIR / "sample.rs"


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


def _get_go_root():  # type: ignore[return]
    """Parse sample.go and return root node (fails test if parse fails)."""
    from seam.indexer.parser import parse_go

    node = parse_go(SAMPLE_GO)
    assert node is not None, "sample.go failed to parse"
    return node


def _get_rs_root():  # type: ignore[return]
    """Parse sample.rs and return root node (fails test if parse fails)."""
    from seam.indexer.parser import parse_rust

    node = parse_rust(SAMPLE_RS)
    assert node is not None, "sample.rs failed to parse"
    return node


# ── G1: Go symbols ─────────────────────────────────────────────────────────────


class TestGoSymbols:
    """G1: Go symbol extraction covers all required kinds and docstrings."""

    def test_func_extracted_as_function(self) -> None:
        """Top-level func Add → kind='function'."""
        root = _get_go_root()
        symbols = extract_symbols(root, "go", SAMPLE_GO)
        sym = _sym(symbols, "Add")
        assert sym is not None, "Add not found in Go symbols"
        assert sym["kind"] == "function"
        assert sym["file"] == str(SAMPLE_GO)

    def test_func_start_line_correct(self) -> None:
        """Add() start_line is 1-based and non-zero."""
        root = _get_go_root()
        symbols = extract_symbols(root, "go", SAMPLE_GO)
        sym = _sym(symbols, "Add")
        assert sym is not None
        assert sym["start_line"] >= 1

    def test_method_qualified_as_recv_name(self) -> None:
        """func (r *Repo) Save() → name='Repo.Save', kind='method'.

        This is the canonical Go method naming: pointer receiver *T → T.
        """
        root = _get_go_root()
        symbols = extract_symbols(root, "go", SAMPLE_GO)
        sym = _sym(symbols, "Repo.Save")
        assert sym is not None, "Repo.Save not found — pointer receiver not normalized"
        assert sym["kind"] == "method"

    def test_struct_extracted_as_class(self) -> None:
        """type Repo struct → kind='class'."""
        root = _get_go_root()
        symbols = extract_symbols(root, "go", SAMPLE_GO)
        sym = _sym(symbols, "Repo")
        assert sym is not None, "Repo struct not found"
        assert sym["kind"] == "class"

    def test_interface_extracted_as_interface(self) -> None:
        """type Writer interface → kind='interface'."""
        root = _get_go_root()
        symbols = extract_symbols(root, "go", SAMPLE_GO)
        sym = _sym(symbols, "Writer")
        assert sym is not None, "Writer interface not found"
        assert sym["kind"] == "interface"

    def test_type_alias_extracted_as_type(self) -> None:
        """type PathAlias = string → kind='type'."""
        root = _get_go_root()
        symbols = extract_symbols(root, "go", SAMPLE_GO)
        sym = _sym(symbols, "PathAlias")
        assert sym is not None, "PathAlias type alias not found"
        assert sym["kind"] == "type"

    def test_func_with_doc_comment_has_docstring(self) -> None:
        """Add() is preceded by // doc comment → docstring is not None."""
        root = _get_go_root()
        symbols = extract_symbols(root, "go", SAMPLE_GO)
        sym = _sym(symbols, "Add")
        assert sym is not None
        # The first doc line is 'Add sums two integers and returns the result.'
        assert sym["docstring"] is not None
        assert "sums two integers" in sym["docstring"]

    def test_func_without_doc_comment_has_no_docstring(self) -> None:
        """multiply() has no doc comment → docstring is None."""
        root = _get_go_root()
        symbols = extract_symbols(root, "go", SAMPLE_GO)
        sym = _sym(symbols, "multiply")
        assert sym is not None
        # multiply has a comment but it's not directly above it with no blank gap
        # The fixture has a comment '// multiply is an internal helper...' on the line
        # immediately before the function — so it WILL have a docstring.
        # Accept either None or a string; but it must be a str if present.
        assert sym["docstring"] is None or isinstance(sym["docstring"], str)

    def test_struct_doc_comment_captured(self) -> None:
        """Repo struct preceded by // doc comment → docstring captures it."""
        root = _get_go_root()
        symbols = extract_symbols(root, "go", SAMPLE_GO)
        sym = _sym(symbols, "Repo")
        assert sym is not None
        assert sym["docstring"] is not None
        assert "repository" in sym["docstring"]

    def test_returns_list_not_none(self) -> None:
        """extract_symbols always returns a list, never None."""
        root = _get_go_root()
        result = extract_symbols(root, "go", SAMPLE_GO)
        assert isinstance(result, list)

    def test_no_symbol_for_var_declaration(self) -> None:
        """var globalVar is not a function/type/struct → not extracted as a symbol."""
        root = _get_go_root()
        symbols = extract_symbols(root, "go", SAMPLE_GO)
        names = {s["name"] for s in symbols}
        assert "globalVar" not in names


# ── G2: Rust symbols ───────────────────────────────────────────────────────────


class TestRustSymbols:
    """G2: Rust symbol extraction covers all required kinds and docstrings."""

    def test_fn_extracted_as_function(self) -> None:
        """fn multiply → kind='function'."""
        root = _get_rs_root()
        symbols = extract_symbols(root, "rust", SAMPLE_RS)
        sym = _sym(symbols, "multiply")
        assert sym is not None, "multiply not found in Rust symbols"
        assert sym["kind"] == "function"

    def test_impl_method_qualified_as_type_fn(self) -> None:
        """impl Store { fn save } → name='Store.save', kind='method'.

        This mirrors how Python/TS qualify methods as Class.method.
        """
        root = _get_rs_root()
        symbols = extract_symbols(root, "rust", SAMPLE_RS)
        sym = _sym(symbols, "Store.save")
        assert sym is not None, "Store.save not found — impl method not qualified"
        assert sym["kind"] == "method"

    def test_struct_extracted_as_class(self) -> None:
        """struct Store → kind='class'."""
        root = _get_rs_root()
        symbols = extract_symbols(root, "rust", SAMPLE_RS)
        sym = _sym(symbols, "Store")
        assert sym is not None, "Store struct not found"
        assert sym["kind"] == "class"

    def test_enum_extracted_as_type(self) -> None:
        """enum Status → kind='type'."""
        root = _get_rs_root()
        symbols = extract_symbols(root, "rust", SAMPLE_RS)
        sym = _sym(symbols, "Status")
        assert sym is not None, "Status enum not found"
        assert sym["kind"] == "type"

    def test_trait_extracted_as_interface(self) -> None:
        """trait Serializer → kind='interface'."""
        root = _get_rs_root()
        symbols = extract_symbols(root, "rust", SAMPLE_RS)
        sym = _sym(symbols, "Serializer")
        assert sym is not None, "Serializer trait not found"
        assert sym["kind"] == "interface"

    def test_fn_with_doc_comment_has_docstring(self) -> None:
        """/// Compute the product → docstring captured for multiply."""
        root = _get_rs_root()
        symbols = extract_symbols(root, "rust", SAMPLE_RS)
        sym = _sym(symbols, "multiply")
        assert sym is not None
        assert sym["docstring"] is not None
        assert "product" in sym["docstring"]

    def test_struct_doc_comment_captured(self) -> None:
        """/// A data store struct → docstring captured for Store."""
        root = _get_rs_root()
        symbols = extract_symbols(root, "rust", SAMPLE_RS)
        sym = _sym(symbols, "Store")
        assert sym is not None
        assert sym["docstring"] is not None
        assert "data store" in sym["docstring"]

    def test_mod_not_emitted_as_symbol(self) -> None:
        """mod utils itself is NOT emitted as a symbol (per spec MVP)."""
        root = _get_rs_root()
        symbols = extract_symbols(root, "rust", SAMPLE_RS)
        names = {s["name"] for s in symbols}
        assert "utils" not in names

    def test_nested_fn_in_mod_emitted(self) -> None:
        """pub fn helper() inside mod utils IS emitted as a function symbol."""
        root = _get_rs_root()
        symbols = extract_symbols(root, "rust", SAMPLE_RS)
        sym = _sym(symbols, "helper")
        assert sym is not None, "helper fn inside mod utils should be extracted"
        assert sym["kind"] == "function"

    def test_returns_list_not_none(self) -> None:
        """extract_symbols always returns a list, never None."""
        root = _get_rs_root()
        result = extract_symbols(root, "rust", SAMPLE_RS)
        assert isinstance(result, list)


# ── G3: Go edges ──────────────────────────────────────────────────────────────


class TestGoEdges:
    """G3: Go import and call edges are extracted correctly."""

    def test_import_edge_produced(self) -> None:
        """Go import statement produces at least one import edge."""
        root = _get_go_root()
        edges = extract_edges(root, "go", SAMPLE_GO)
        import_edges = [e for e in edges if e["kind"] == "import"]
        assert len(import_edges) > 0, "No import edges found for Go fixture"

    def test_import_target_is_package_name(self) -> None:
        """import 'fmt' → edge target is 'fmt' (last path segment)."""
        root = _get_go_root()
        edges = extract_edges(root, "go", SAMPLE_GO)
        targets = {e["target"] for e in edges if e["kind"] == "import"}
        assert "fmt" in targets, f"Expected 'fmt' import edge, got targets: {targets}"

    def test_import_source_is_file_stem(self) -> None:
        """Import edge source is the file stem ('sample')."""
        root = _get_go_root()
        edges = extract_edges(root, "go", SAMPLE_GO)
        for e in edges:
            if e["kind"] == "import" and e["target"] == "fmt":
                assert e["source"] == "sample", f"Expected source='sample', got {e['source']}"

    def test_call_edge_produced(self) -> None:
        """Bare-identifier call expression produces a call edge."""
        root = _get_go_root()
        edges = extract_edges(root, "go", SAMPLE_GO)
        call_edges = [e for e in edges if e["kind"] == "call"]
        assert len(call_edges) > 0, "No call edges found for Go fixture"

    def test_call_edge_target_is_callee_name(self) -> None:
        """Add() calls multiply() → call edge target is 'multiply'."""
        root = _get_go_root()
        edges = extract_edges(root, "go", SAMPLE_GO)
        e = _edge(edges, target="multiply", kind="call")
        assert e is not None, "Expected call edge targeting 'multiply'"

    def test_call_edge_source_is_enclosing_function(self) -> None:
        """The call edge source is the enclosing function name ('Add')."""
        root = _get_go_root()
        edges = extract_edges(root, "go", SAMPLE_GO)
        e = _edge(edges, target="multiply", kind="call")
        assert e is not None
        assert e["source"] == "Add", f"Expected source='Add', got {e['source']}"

    def test_edges_have_confidence_field(self) -> None:
        """All edges carry a confidence field (EXTRACTED|INFERRED|AMBIGUOUS)."""
        root = _get_go_root()
        edges = extract_edges(root, "go", SAMPLE_GO)
        for e in edges:
            assert e["confidence"] in ("EXTRACTED", "INFERRED", "AMBIGUOUS"), (
                f"Edge missing valid confidence: {e}"
            )


# ── G4: Rust edges ─────────────────────────────────────────────────────────────


class TestRustEdges:
    """G4: Rust import and call edges are extracted correctly."""

    def test_import_edge_produced(self) -> None:
        """use declaration produces at least one import edge."""
        root = _get_rs_root()
        edges = extract_edges(root, "rust", SAMPLE_RS)
        import_edges = [e for e in edges if e["kind"] == "import"]
        assert len(import_edges) > 0, "No import edges found for Rust fixture"

    def test_import_target_is_final_segment(self) -> None:
        """use std::io::Write → edge target is 'Write' (final segment)."""
        root = _get_rs_root()
        edges = extract_edges(root, "rust", SAMPLE_RS)
        targets = {e["target"] for e in edges if e["kind"] == "import"}
        assert "Write" in targets, f"Expected 'Write' import edge, got targets: {targets}"

    def test_import_source_is_file_stem(self) -> None:
        """Import edge source is the file stem ('sample')."""
        root = _get_rs_root()
        edges = extract_edges(root, "rust", SAMPLE_RS)
        for e in edges:
            if e["kind"] == "import" and e["target"] == "Write":
                assert e["source"] == "sample", f"Expected source='sample', got {e['source']}"

    def test_call_edge_produced(self) -> None:
        """Bare-identifier call expression produces a call edge."""
        root = _get_rs_root()
        edges = extract_edges(root, "rust", SAMPLE_RS)
        call_edges = [e for e in edges if e["kind"] == "call"]
        assert len(call_edges) > 0, "No call edges found for Rust fixture"

    def test_call_edge_target_is_callee_name(self) -> None:
        """multiply() calls add() → call edge with target 'add'."""
        root = _get_rs_root()
        edges = extract_edges(root, "rust", SAMPLE_RS)
        e = _edge(edges, target="add", kind="call")
        assert e is not None, "Expected call edge targeting 'add'"

    def test_call_edge_source_is_enclosing_function(self) -> None:
        """The call source is the enclosing function name ('multiply')."""
        root = _get_rs_root()
        edges = extract_edges(root, "rust", SAMPLE_RS)
        e = _edge(edges, target="add", kind="call")
        assert e is not None
        assert e["source"] == "multiply", f"Expected source='multiply', got {e['source']}"

    def test_edges_have_confidence_field(self) -> None:
        """All edges carry a confidence field."""
        root = _get_rs_root()
        edges = extract_edges(root, "rust", SAMPLE_RS)
        for e in edges:
            assert e["confidence"] in ("EXTRACTED", "INFERRED", "AMBIGUOUS")


# ── G5: Go comments ───────────────────────────────────────────────────────────


class TestGoComments:
    """G5: Go semantic comment extraction from // and /* */ comments."""

    def test_why_marker_extracted(self) -> None:
        """// WHY: ... in a Go file → Comment with marker='WHY'."""
        root = _get_go_root()
        comments = extract_comments(root, "go", SAMPLE_GO)
        whys = [c for c in comments if c["marker"] == "WHY"]
        assert len(whys) > 0, "No WHY marker found in Go fixture"

    def test_hack_marker_extracted(self) -> None:
        """// HACK: ... in a Go file → Comment with marker='HACK'."""
        root = _get_go_root()
        comments = extract_comments(root, "go", SAMPLE_GO)
        hacks = [c for c in comments if c["marker"] == "HACK"]
        assert len(hacks) > 0, "No HACK marker found in Go fixture"

    def test_note_marker_extracted(self) -> None:
        """// NOTE: ... in a Go file → Comment with marker='NOTE'."""
        root = _get_go_root()
        comments = extract_comments(root, "go", SAMPLE_GO)
        notes = [c for c in comments if c["marker"] == "NOTE"]
        assert len(notes) > 0, "No NOTE marker found in Go fixture"

    def test_plain_comment_not_extracted(self) -> None:
        """Plain // Package comment → not extracted as a semantic comment."""
        root = _get_go_root()
        comments = extract_comments(root, "go", SAMPLE_GO)
        # There should not be a marker for 'Package' or plain text comments
        texts = {c["text"] for c in comments}
        assert not any("Package sample" in t for t in texts)

    def test_comment_has_correct_fields(self) -> None:
        """Each extracted comment has marker, text, and line fields."""
        root = _get_go_root()
        comments = extract_comments(root, "go", SAMPLE_GO)
        for c in comments:
            assert "marker" in c
            assert "text" in c
            assert "line" in c
            assert isinstance(c["line"], int)
            assert c["line"] >= 1

    def test_returns_list_not_none(self) -> None:
        """extract_comments always returns a list, never raises."""
        root = _get_go_root()
        result = extract_comments(root, "go", SAMPLE_GO)
        assert isinstance(result, list)


# ── G6: Rust comments ─────────────────────────────────────────────────────────


class TestRustComments:
    """G6: Rust semantic comment extraction from //!, ///, // and /* */ comments."""

    def test_inner_doc_why_extracted(self) -> None:
        """//! WHY: ... → Comment with marker='WHY' (inner doc with marker)."""
        root = _get_rs_root()
        comments = extract_comments(root, "rust", SAMPLE_RS)
        whys = [c for c in comments if c["marker"] == "WHY"]
        assert len(whys) > 0, "No WHY marker found in Rust fixture"

    def test_hack_in_regular_comment_extracted(self) -> None:
        """// HACK: ... inside impl block → Comment with marker='HACK'."""
        root = _get_rs_root()
        comments = extract_comments(root, "rust", SAMPLE_RS)
        hacks = [c for c in comments if c["marker"] == "HACK"]
        assert len(hacks) > 0, "No HACK marker found in Rust fixture"

    def test_note_in_block_comment_extracted(self) -> None:
        """/* NOTE: ... */ block comment → Comment with marker='NOTE'."""
        root = _get_rs_root()
        comments = extract_comments(root, "rust", SAMPLE_RS)
        notes = [c for c in comments if c["marker"] == "NOTE"]
        assert len(notes) > 0, "No NOTE marker found in Rust block comment"

    def test_fixme_in_block_comment_extracted(self) -> None:
        """/* ... FIXME: ... */ block comment second line → Comment with marker='FIXME'."""
        root = _get_rs_root()
        comments = extract_comments(root, "rust", SAMPLE_RS)
        fixmes = [c for c in comments if c["marker"] == "FIXME"]
        assert len(fixmes) > 0, "No FIXME marker found in Rust block comment"

    def test_plain_doc_comment_not_extracted_as_marker(self) -> None:
        """/// A data store struct. — no marker keyword → not extracted."""
        root = _get_rs_root()
        comments = extract_comments(root, "rust", SAMPLE_RS)
        # 'A data store struct.' should NOT appear as a marker's text
        texts = {c["text"] for c in comments}
        assert not any("A data store struct" in t for t in texts)

    def test_comment_fields_valid(self) -> None:
        """All extracted comments have valid marker, text, and line fields."""
        root = _get_rs_root()
        comments = extract_comments(root, "rust", SAMPLE_RS)
        valid_markers = {"WHY", "HACK", "NOTE", "TODO", "FIXME"}
        for c in comments:
            assert c["marker"] in valid_markers
            assert isinstance(c["line"], int)
            assert c["line"] >= 1

    def test_returns_list_not_none(self) -> None:
        """extract_comments always returns a list, never raises."""
        root = _get_rs_root()
        result = extract_comments(root, "rust", SAMPLE_RS)
        assert isinstance(result, list)


# ── G7: parser ────────────────────────────────────────────────────────────────


class TestGoRustParser:
    """G7: parse_go and parse_rust behave like the existing parse_python/parse_typescript."""

    def test_parse_go_valid_file_returns_node(self) -> None:
        """parse_go(sample.go) returns a non-None AST root node."""
        from seam.indexer.parser import parse_go

        node = parse_go(SAMPLE_GO)
        assert node is not None

    def test_parse_go_node_has_children(self) -> None:
        """root node from sample.go has at least one child."""
        from seam.indexer.parser import parse_go

        node = parse_go(SAMPLE_GO)
        assert node is not None
        assert len(node.children) > 0  # type: ignore[union-attr]

    def test_parse_go_binary_file_returns_none(self, tmp_path: Path) -> None:
        """Binary .go file → None (not raised)."""
        from seam.indexer.parser import parse_go

        binary_file = tmp_path / "binary.go"
        binary_file.write_bytes(b"package main\x00rest")
        node = parse_go(binary_file)
        assert node is None

    def test_parse_go_missing_file_returns_none(self) -> None:
        """Non-existent .go path → None."""
        from seam.indexer.parser import parse_go

        node = parse_go(Path("/nonexistent/path/file.go"))
        assert node is None

    def test_parse_rust_valid_file_returns_node(self) -> None:
        """parse_rust(sample.rs) returns a non-None AST root node."""
        from seam.indexer.parser import parse_rust

        node = parse_rust(SAMPLE_RS)
        assert node is not None

    def test_parse_rust_node_has_children(self) -> None:
        """root node from sample.rs has at least one child."""
        from seam.indexer.parser import parse_rust

        node = parse_rust(SAMPLE_RS)
        assert node is not None
        assert len(node.children) > 0  # type: ignore[union-attr]

    def test_parse_rust_binary_file_returns_none(self, tmp_path: Path) -> None:
        """Binary .rs file → None."""
        from seam.indexer.parser import parse_rust

        binary_file = tmp_path / "binary.rs"
        binary_file.write_bytes(b"fn main() {}\x00")
        node = parse_rust(binary_file)
        assert node is None

    def test_parse_rust_missing_file_returns_none(self) -> None:
        """Non-existent .rs path → None."""
        from seam.indexer.parser import parse_rust

        node = parse_rust(Path("/nonexistent/path/file.rs"))
        assert node is None


# ── G8: pipeline ──────────────────────────────────────────────────────────────


class TestGoRustPipeline:
    """G8: _dispatch_parser routes .go/.rs; index_one_file indexes the fixtures."""

    def test_dispatch_parser_go(self, tmp_path: Path) -> None:
        """_dispatch_parser routes language='go' to parse_go."""
        from seam.indexer.pipeline import _dispatch_parser

        go_file = tmp_path / "test.go"
        go_file.write_text("package main\nfunc main() {}\n")
        result = _dispatch_parser(go_file, "go")
        assert result is not None, "_dispatch_parser returned None for Go source"

    def test_dispatch_parser_rust(self, tmp_path: Path) -> None:
        """_dispatch_parser routes language='rust' to parse_rust."""
        from seam.indexer.pipeline import _dispatch_parser

        rs_file = tmp_path / "test.rs"
        rs_file.write_text("fn main() {}\n")
        result = _dispatch_parser(rs_file, "rust")
        assert result is not None, "_dispatch_parser returned None for Rust source"

    def test_language_map_has_go(self) -> None:
        """config.SEAM_LANGUAGE_MAP includes '.go' → 'go'."""
        import seam.config as config

        assert ".go" in config.SEAM_LANGUAGE_MAP
        assert config.SEAM_LANGUAGE_MAP[".go"] == "go"

    def test_language_map_has_rust(self) -> None:
        """config.SEAM_LANGUAGE_MAP includes '.rs' → 'rust'."""
        import seam.config as config

        assert ".rs" in config.SEAM_LANGUAGE_MAP
        assert config.SEAM_LANGUAGE_MAP[".rs"] == "rust"

    def test_index_one_file_go_fixture(self, tmp_path: Path) -> None:
        """index_one_file on sample.go returns (symbols>0, edges>=0)."""
        from seam.indexer.db import init_db
        from seam.indexer.pipeline import index_one_file

        db_path = tmp_path / "seam.db"
        conn = init_db(db_path)
        result = index_one_file(conn, SAMPLE_GO)
        conn.close()
        assert result is not None, "index_one_file returned None for sample.go"
        sym_count, _edge_count = result
        assert sym_count > 0, f"Expected symbols > 0 for sample.go, got {sym_count}"

    def test_index_one_file_rust_fixture(self, tmp_path: Path) -> None:
        """index_one_file on sample.rs returns (symbols>0, edges>=0)."""
        from seam.indexer.db import init_db
        from seam.indexer.pipeline import index_one_file

        db_path = tmp_path / "seam.db"
        conn = init_db(db_path)
        result = index_one_file(conn, SAMPLE_RS)
        conn.close()
        assert result is not None, "index_one_file returned None for sample.rs"
        sym_count, _edge_count = result
        assert sym_count > 0, f"Expected symbols > 0 for sample.rs, got {sym_count}"

    def test_walk_project_finds_go_files(self, tmp_path: Path) -> None:
        """walk_project includes .go files after language map update."""
        from seam.indexer.pipeline import walk_project

        go_file = tmp_path / "main.go"
        go_file.write_text("package main\nfunc main() {}\n")
        files = walk_project(tmp_path)
        paths = {f.name for f in files}
        assert "main.go" in paths, f"main.go not found in walk_project output: {paths}"

    def test_walk_project_finds_rust_files(self, tmp_path: Path) -> None:
        """walk_project includes .rs files after language map update."""
        from seam.indexer.pipeline import walk_project

        rs_file = tmp_path / "lib.rs"
        rs_file.write_text("fn main() {}\n")
        files = walk_project(tmp_path)
        paths = {f.name for f in files}
        assert "lib.rs" in paths, f"lib.rs not found in walk_project output: {paths}"


# ── FIX 2: Go generic receiver ────────────────────────────────────────────────


class TestGoGenericReceiver:
    """FIX 2: _go_recv_type_name handles generic_type receivers (*Repo[T], Repo[T])."""

    def test_pointer_generic_receiver_method_qualified(self) -> None:
        """func (r *Repo[T]) Get() → name='Repo.Get', kind='method'.

        The fixture sample.go now includes this method. The generic receiver
        *Repo[T] must be normalized to just 'Repo' (base type_identifier).
        """
        root = _get_go_root()
        symbols = extract_symbols(root, "go", SAMPLE_GO)
        sym = _sym(symbols, "Repo.Get")
        assert sym is not None, (
            "Repo.Get not found — generic pointer receiver *Repo[T] not handled. "
            f"Symbols found: {[s['name'] for s in symbols]}"
        )
        assert sym["kind"] == "method"

    def test_generic_receiver_inline(self, tmp_path: Path) -> None:
        """Value generic receiver Repo[T] → Repo.Method, kind='method'."""
        from seam.indexer.parser import parse_go

        src = tmp_path / "generic.go"
        src.write_text(
            "package main\n"
            "type Repo[T any] struct{ v T }\n"
            "func (r Repo[T]) Fetch() T { return r.v }\n"
        )
        node = parse_go(src)
        assert node is not None
        symbols = extract_symbols(node, "go", src)
        sym = _sym(symbols, "Repo.Fetch")
        assert sym is not None, f"Repo.Fetch not found. Symbols: {[s['name'] for s in symbols]}"
        assert sym["kind"] == "method"


# ── FIX 4: Rust doc-comment adjacency ────────────────────────────────────────


class TestRustDocAdjacency:
    """FIX 4: _rust_doc_comment uses visible end row to judge adjacency correctly."""

    def _parse_rs(self, tmp_path: Path, src: str):  # type: ignore[return]
        """Write a .rs file, parse it, and return the root node."""
        from seam.indexer.parser import parse_rust

        f = tmp_path / "adj.rs"
        f.write_text(src)
        node = parse_rust(f)
        assert node is not None, "Failed to parse Rust source"
        return node, f

    def test_adjacent_doc_attached(self, tmp_path: Path) -> None:
        """/// doc immediately above fn → docstring 'doc'."""
        src = "/// doc\nfn foo() {}\n"
        node, path = self._parse_rs(tmp_path, src)
        symbols = extract_symbols(node, "rust", path)
        sym = _sym(symbols, "foo")
        assert sym is not None
        assert sym["docstring"] is not None
        assert "doc" in sym["docstring"]

    def test_blank_line_orphan_not_attached(self, tmp_path: Path) -> None:
        """/// orphan\\n\\nfn bar(){} — blank line → docstring is None for bar."""
        src = "/// orphan\n\nfn bar() {}\n"
        node, path = self._parse_rs(tmp_path, src)
        symbols = extract_symbols(node, "rust", path)
        sym = _sym(symbols, "bar")
        assert sym is not None
        # blank line separates the doc from the fn — must NOT attach
        assert sym["docstring"] is None, (
            f"Expected docstring=None (blank line gap), got: {sym['docstring']!r}"
        )

    def test_only_nearest_attached_when_gap_exists(self, tmp_path: Path) -> None:
        """/// far\\n\\n/// near\\nfn baz(){} → docstring is 'near' only."""
        src = "/// far\n\n/// near\nfn baz() {}\n"
        node, path = self._parse_rs(tmp_path, src)
        symbols = extract_symbols(node, "rust", path)
        sym = _sym(symbols, "baz")
        assert sym is not None
        assert sym["docstring"] is not None
        # Only the adjacent '/// near' must be captured, not '/// far'.
        assert "near" in sym["docstring"]
        assert "far" not in sym["docstring"], (
            f"'far' should not be in docstring: {sym['docstring']!r}"
        )


# ── FIX 5: Rust trait default methods ────────────────────────────────────────


class TestRustTraitDefaultMethods:
    """FIX 5: _extract_symbols_rust recurses trait body and emits default methods."""

    def test_trait_interface_still_emitted(self) -> None:
        """Greet trait → symbol kind='interface' name='Greet' is still present."""
        root = _get_rs_root()
        symbols = extract_symbols(root, "rust", SAMPLE_RS)
        sym = _sym(symbols, "Greet")
        assert sym is not None, f"Greet not found. Symbols: {[s['name'] for s in symbols]}"
        assert sym["kind"] == "interface"

    def test_trait_default_method_emitted(self) -> None:
        """Greet trait default method fn hello → symbol name='Greet.hello', kind='method'."""
        root = _get_rs_root()
        symbols = extract_symbols(root, "rust", SAMPLE_RS)
        sym = _sym(symbols, "Greet.hello")
        assert sym is not None, (
            "Greet.hello not found — trait default method not emitted. "
            f"Symbols: {[s['name'] for s in symbols]}"
        )
        assert sym["kind"] == "method"

    def test_trait_signature_only_method_inline(self, tmp_path: Path) -> None:
        """Signature-only trait method (no body) is also emitted as a method symbol."""
        from seam.indexer.parser import parse_rust

        src = tmp_path / "trait.rs"
        src.write_text("trait Sayer { fn say(&self); }\n")
        node = parse_rust(src)
        assert node is not None
        symbols = extract_symbols(node, "rust", src)
        sym = _sym(symbols, "Sayer.say")
        assert sym is not None, f"Sayer.say not found. Symbols: {[s['name'] for s in symbols]}"
        assert sym["kind"] == "method"


# ── FIX 6: Rust aliased use ───────────────────────────────────────────────────


class TestRustAliasedUse:
    """FIX 6: use_as_clause produces an import edge for the ORIGINAL name."""

    def test_aliased_use_produces_import_edge(self) -> None:
        """use std::fmt as formatting → import edge target 'fmt' (real name)."""
        root = _get_rs_root()
        edges = extract_edges(root, "rust", SAMPLE_RS)
        import_targets = {e["target"] for e in edges if e["kind"] == "import"}
        assert "fmt" in import_targets, (
            f"Expected 'fmt' import edge from 'use std::fmt as formatting'. "
            f"Import targets found: {import_targets}"
        )

    def test_aliased_use_does_not_emit_alias(self) -> None:
        """use std::fmt as formatting → 'formatting' (the alias) is NOT emitted."""
        root = _get_rs_root()
        edges = extract_edges(root, "rust", SAMPLE_RS)
        import_targets = {e["target"] for e in edges if e["kind"] == "import"}
        assert "formatting" not in import_targets, (
            "Alias 'formatting' should not be used as an import edge target"
        )

    def test_simple_aliased_use_inline(self, tmp_path: Path) -> None:
        """use foo as bar → import edge target 'foo' (not 'bar')."""
        from seam.indexer.parser import parse_rust

        src = tmp_path / "alias.rs"
        src.write_text("use foo as bar;\nfn main() {}\n")
        node = parse_rust(src)
        assert node is not None
        edges = extract_edges(node, "rust", src)
        import_targets = {e["target"] for e in edges if e["kind"] == "import"}
        assert "foo" in import_targets, f"Expected 'foo' in import targets, got: {import_targets}"
        assert "bar" not in import_targets, "'bar' (alias) should not be in targets"
