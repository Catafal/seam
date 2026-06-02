"""Tests for Ruby and PHP language support (Phase 9).

TDD: Tests written before implementation. Each group covers one behavioral slice:

R1 — Ruby symbols:  module→class, class→class, method→Class.method,
                    singleton_method→Class.method, top-level def→function,
                    doc-comment (leading # block) → docstring.
R2 — PHP symbols:   class→class, interface→interface, trait→interface,
                    enum→type, function→function, method→Class.method,
                    phpdoc (/** */) → docstring.
R3 — Ruby edges:    require → import edge; require_relative → import edge;
                    bare call → call edge (no receiver).
R4 — PHP edges:     namespace_use_declaration → import edge;
                    function_call_expression → call edge (bare only).
R5 — Ruby comments: WHY/HACK/NOTE markers extracted from # comments.
R6 — PHP comments:  WHY/HACK/NOTE markers from // , # , and /** */ comments.
R7 — parser:        parse_ruby / parse_php return Node for valid source, None for binary.
R8 — pipeline:      _dispatch_parser routes .rb/.php; index_one_file indexes fixtures.
R9 — signatures:    Ruby signature non-None; PHP signature + visibility + decorators.
R10 — imports:      Ruby require binding extracted; PHP use binding extracted.
R11 — builtins:     is_builtin True for known Ruby/PHP builtins, False for repo names.

All assertions go through the PUBLIC API (extract_symbols / extract_edges /
extract_comments / extract_node_fields / extract_import_mappings / is_builtin),
never against internals.
"""

from pathlib import Path

from seam.indexer.graph import Edge, Symbol, extract_comments, extract_edges, extract_symbols

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"
SAMPLE_RB = FIXTURES_DIR / "sample.rb"
SAMPLE_PHP = FIXTURES_DIR / "sample.php"


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


def _get_rb_root():  # type: ignore[return]
    """Parse sample.rb and return root node (fails test if parse fails)."""
    from seam.indexer.parser import parse_ruby

    node = parse_ruby(SAMPLE_RB)
    assert node is not None, "sample.rb failed to parse"
    return node


def _get_php_root():  # type: ignore[return]
    """Parse sample.php and return root node (fails test if parse fails)."""
    from seam.indexer.parser import parse_php

    node = parse_php(SAMPLE_PHP)
    assert node is not None, "sample.php failed to parse"
    return node


# ── R1: Ruby symbols ──────────────────────────────────────────────────────────


class TestRubySymbols:
    """R1: Ruby symbol extraction covers all required kinds and docstrings."""

    def test_module_extracted_as_class(self) -> None:
        """module Utils → kind='class' (named container per spec).

        Ruby module is the closest fit to 'class' in the closed vocabulary.
        """
        root = _get_rb_root()
        symbols = extract_symbols(root, "ruby", SAMPLE_RB)
        sym = _sym(symbols, "Utils")
        assert sym is not None, f"Utils module not found. Symbols: {[s['name'] for s in symbols]}"
        assert sym["kind"] == "class"

    def test_class_extracted_as_class(self) -> None:
        """class Person → kind='class'."""
        root = _get_rb_root()
        symbols = extract_symbols(root, "ruby", SAMPLE_RB)
        sym = _sym(symbols, "Person")
        assert sym is not None, "Person not found"
        assert sym["kind"] == "class"

    def test_method_inside_class_qualified(self) -> None:
        """def greet inside class Person → name='Person.greet', kind='method'."""
        root = _get_rb_root()
        symbols = extract_symbols(root, "ruby", SAMPLE_RB)
        sym = _sym(symbols, "Person.greet")
        assert sym is not None, f"Person.greet not found. Symbols: {[s['name'] for s in symbols]}"
        assert sym["kind"] == "method"

    def test_singleton_method_inside_class_qualified(self) -> None:
        """def self.create inside Person → name='Person.create', kind='method'."""
        root = _get_rb_root()
        symbols = extract_symbols(root, "ruby", SAMPLE_RB)
        sym = _sym(symbols, "Person.create")
        assert sym is not None, (
            f"Person.create (singleton_method) not found. Symbols: {[s['name'] for s in symbols]}"
        )
        assert sym["kind"] == "method"

    def test_initialize_inside_class_qualified(self) -> None:
        """def initialize inside Person → name='Person.initialize', kind='method'."""
        root = _get_rb_root()
        symbols = extract_symbols(root, "ruby", SAMPLE_RB)
        sym = _sym(symbols, "Person.initialize")
        assert sym is not None, "Person.initialize not found"
        assert sym["kind"] == "method"

    def test_toplevel_method_extracted_as_function(self) -> None:
        """def say_hello at top level → kind='function'."""
        root = _get_rb_root()
        symbols = extract_symbols(root, "ruby", SAMPLE_RB)
        sym = _sym(symbols, "say_hello")
        assert sym is not None, "say_hello not found"
        assert sym["kind"] == "function"

    def test_module_method_qualified(self) -> None:
        """def self.fmt inside module Utils → name='Utils.fmt', kind='method'."""
        root = _get_rb_root()
        symbols = extract_symbols(root, "ruby", SAMPLE_RB)
        sym = _sym(symbols, "Utils.fmt")
        assert sym is not None, f"Utils.fmt not found. Symbols: {[s['name'] for s in symbols]}"
        assert sym["kind"] == "method"

    def test_method_has_docstring(self) -> None:
        """def greet is preceded by '# Greet the person...' → docstring captured."""
        root = _get_rb_root()
        symbols = extract_symbols(root, "ruby", SAMPLE_RB)
        sym = _sym(symbols, "Person.greet")
        assert sym is not None
        assert sym["docstring"] is not None
        assert "Greet" in sym["docstring"]

    def test_class_has_docstring(self) -> None:
        """class Person preceded by '# A person domain object' → docstring captured."""
        root = _get_rb_root()
        symbols = extract_symbols(root, "ruby", SAMPLE_RB)
        sym = _sym(symbols, "Person")
        assert sym is not None
        assert sym["docstring"] is not None
        assert "person" in sym["docstring"].lower()

    def test_returns_list_not_none(self) -> None:
        """extract_symbols always returns a list, never raises."""
        root = _get_rb_root()
        result = extract_symbols(root, "ruby", SAMPLE_RB)
        assert isinstance(result, list)

    def test_symbol_has_file_field(self) -> None:
        """Extracted symbols carry the correct file path."""
        root = _get_rb_root()
        symbols = extract_symbols(root, "ruby", SAMPLE_RB)
        for s in symbols:
            assert s["file"] == str(SAMPLE_RB)

    def test_symbol_start_line_positive(self) -> None:
        """All extracted symbols have start_line >= 1."""
        root = _get_rb_root()
        symbols = extract_symbols(root, "ruby", SAMPLE_RB)
        for s in symbols:
            assert s["start_line"] >= 1


# ── R2: PHP symbols ───────────────────────────────────────────────────────────


class TestPhpSymbols:
    """R2: PHP symbol extraction covers all required kinds and docstrings."""

    def test_class_extracted_as_class(self) -> None:
        """class UserController → kind='class'."""
        root = _get_php_root()
        symbols = extract_symbols(root, "php", SAMPLE_PHP)
        sym = _sym(symbols, "UserController")
        assert sym is not None, f"UserController not found. Symbols: {[s['name'] for s in symbols]}"
        assert sym["kind"] == "class"

    def test_interface_extracted_as_interface(self) -> None:
        """interface Loggable → kind='interface'."""
        root = _get_php_root()
        symbols = extract_symbols(root, "php", SAMPLE_PHP)
        sym = _sym(symbols, "Loggable")
        assert sym is not None, "Loggable interface not found"
        assert sym["kind"] == "interface"

    def test_trait_extracted_as_interface(self) -> None:
        """trait HasTimestamps → kind='interface' (closest fit per spec)."""
        root = _get_php_root()
        symbols = extract_symbols(root, "php", SAMPLE_PHP)
        sym = _sym(symbols, "HasTimestamps")
        assert sym is not None, "HasTimestamps trait not found"
        assert sym["kind"] == "interface"

    def test_enum_extracted_as_type(self) -> None:
        """enum Status → kind='type'."""
        root = _get_php_root()
        symbols = extract_symbols(root, "php", SAMPLE_PHP)
        sym = _sym(symbols, "Status")
        assert sym is not None, "Status enum not found"
        assert sym["kind"] == "type"

    def test_function_extracted_as_function(self) -> None:
        """function getUsers() → kind='function'."""
        root = _get_php_root()
        symbols = extract_symbols(root, "php", SAMPLE_PHP)
        sym = _sym(symbols, "getUsers")
        assert sym is not None, "getUsers not found"
        assert sym["kind"] == "function"

    def test_method_qualified_as_class_method(self) -> None:
        """public function index() inside UserController → name='UserController.index', kind='method'."""
        root = _get_php_root()
        symbols = extract_symbols(root, "php", SAMPLE_PHP)
        sym = _sym(symbols, "UserController.index")
        assert sym is not None, (
            f"UserController.index not found. Symbols: {[s['name'] for s in symbols]}"
        )
        assert sym["kind"] == "method"

    def test_protected_method_qualified(self) -> None:
        """protected function findUser() → name='UserController.findUser', kind='method'."""
        root = _get_php_root()
        symbols = extract_symbols(root, "php", SAMPLE_PHP)
        sym = _sym(symbols, "UserController.findUser")
        assert sym is not None, "UserController.findUser not found"
        assert sym["kind"] == "method"

    def test_class_phpdoc_captured(self) -> None:
        """/** UserController handles... */ above class → docstring captured."""
        root = _get_php_root()
        symbols = extract_symbols(root, "php", SAMPLE_PHP)
        sym = _sym(symbols, "UserController")
        assert sym is not None
        assert sym["docstring"] is not None
        assert "UserController" in sym["docstring"]

    def test_function_phpdoc_captured(self) -> None:
        """/** Get all users... */ above getUsers() → docstring captured."""
        root = _get_php_root()
        symbols = extract_symbols(root, "php", SAMPLE_PHP)
        sym = _sym(symbols, "getUsers")
        assert sym is not None
        assert sym["docstring"] is not None
        assert "users" in sym["docstring"].lower()

    def test_returns_list_not_none(self) -> None:
        """extract_symbols always returns a list, never raises."""
        root = _get_php_root()
        result = extract_symbols(root, "php", SAMPLE_PHP)
        assert isinstance(result, list)


# ── R3: Ruby edges ─────────────────────────────────────────────────────────────


class TestRubyEdges:
    """R3: Ruby import and call edges are extracted correctly."""

    def test_require_import_edge(self) -> None:
        """require 'json' → import edge with target='json'."""
        root = _get_rb_root()
        edges = extract_edges(root, "ruby", SAMPLE_RB)
        import_edges = [e for e in edges if e["kind"] == "import"]
        assert len(import_edges) > 0, "No import edges found"
        targets = {e["target"] for e in import_edges}
        assert "json" in targets, f"Expected 'json' import, got: {targets}"

    def test_require_relative_import_edge(self) -> None:
        """require_relative './helper' → import edge with target='helper'."""
        root = _get_rb_root()
        edges = extract_edges(root, "ruby", SAMPLE_RB)
        targets = {e["target"] for e in edges if e["kind"] == "import"}
        assert "helper" in targets, f"Expected 'helper' import, got: {targets}"

    def test_import_source_is_file_stem(self) -> None:
        """Import edge source is the file stem ('sample')."""
        root = _get_rb_root()
        edges = extract_edges(root, "ruby", SAMPLE_RB)
        for e in edges:
            if e["kind"] == "import" and e["target"] == "json":
                assert e["source"] == "sample", f"Expected source='sample', got {e['source']}"

    def test_call_edge_produced(self) -> None:
        """Bare-identifier call inside a method → call edge."""
        root = _get_rb_root()
        edges = extract_edges(root, "ruby", SAMPLE_RB)
        call_edges = [e for e in edges if e["kind"] == "call"]
        assert len(call_edges) > 0, "No call edges found"

    def test_bare_call_target_extracted(self) -> None:
        """Person.greet calls say_hello → call edge target='say_hello'."""
        root = _get_rb_root()
        edges = extract_edges(root, "ruby", SAMPLE_RB)
        e = _edge(edges, target="say_hello", kind="call")
        assert e is not None, (
            "Expected call edge for say_hello. "
            f"Call edge targets: {[x['target'] for x in edges if x['kind'] == 'call']}"
        )

    def test_call_edge_source_is_enclosing_method(self) -> None:
        """say_hello call inside Person.greet → source='Person.greet'."""
        root = _get_rb_root()
        edges = extract_edges(root, "ruby", SAMPLE_RB)
        e = _edge(edges, target="say_hello", kind="call")
        assert e is not None
        assert e["source"] == "Person.greet", f"Expected source='Person.greet', got {e['source']}"

    def test_receiver_call_not_extracted(self) -> None:
        """obj.to_s receiver call → NOT emitted as a call edge (bare only)."""
        root = _get_rb_root()
        edges = extract_edges(root, "ruby", SAMPLE_RB)
        targets = {e["target"] for e in edges if e["kind"] == "call"}
        # 'to_s' is called via receiver (x.to_s) in Utils.fmt → should not appear
        assert "to_s" not in targets, "Receiver call 'to_s' should not be a call edge"

    def test_edges_have_confidence_field(self) -> None:
        """All edges carry a confidence field."""
        root = _get_rb_root()
        edges = extract_edges(root, "ruby", SAMPLE_RB)
        for e in edges:
            assert e["confidence"] in ("EXTRACTED", "INFERRED", "AMBIGUOUS")


# ── R4: PHP edges ──────────────────────────────────────────────────────────────


class TestPhpEdges:
    """R4: PHP import and call edges are extracted correctly."""

    def test_use_declaration_import_edge(self) -> None:
        """use App\\Models\\User; → import edge with target='User'."""
        root = _get_php_root()
        edges = extract_edges(root, "php", SAMPLE_PHP)
        import_edges = [e for e in edges if e["kind"] == "import"]
        assert len(import_edges) > 0, "No import edges found"
        targets = {e["target"] for e in import_edges}
        assert "User" in targets, f"Expected 'User' import edge, got: {targets}"

    def test_use_second_import_edge(self) -> None:
        """use App\\Services\\Logger; → import edge with target='Logger'."""
        root = _get_php_root()
        edges = extract_edges(root, "php", SAMPLE_PHP)
        targets = {e["target"] for e in edges if e["kind"] == "import"}
        assert "Logger" in targets, f"Expected 'Logger' import edge, got: {targets}"

    def test_import_source_is_file_stem(self) -> None:
        """Import edge source is the file stem ('sample')."""
        root = _get_php_root()
        edges = extract_edges(root, "php", SAMPLE_PHP)
        for e in edges:
            if e["kind"] == "import" and e["target"] == "User":
                assert e["source"] == "sample", f"Expected source='sample', got {e['source']}"

    def test_call_edge_produced(self) -> None:
        """function_call_expression → call edge."""
        root = _get_php_root()
        edges = extract_edges(root, "php", SAMPLE_PHP)
        call_edges = [e for e in edges if e["kind"] == "call"]
        assert len(call_edges) > 0, "No call edges found"

    def test_bare_call_target_extracted(self) -> None:
        """$result = getUsers() inside index() → call edge target='getUsers'."""
        root = _get_php_root()
        edges = extract_edges(root, "php", SAMPLE_PHP)
        e = _edge(edges, target="getUsers", kind="call")
        assert e is not None, (
            "Expected call edge for getUsers. "
            f"Call targets: {[x['target'] for x in edges if x['kind'] == 'call']}"
        )

    def test_call_edge_source_is_enclosing_method(self) -> None:
        """getUsers() call inside UserController.index → source='UserController.index'."""
        root = _get_php_root()
        edges = extract_edges(root, "php", SAMPLE_PHP)
        e = _edge(edges, target="getUsers", kind="call")
        assert e is not None
        assert e["source"] == "UserController.index", (
            f"Expected source='UserController.index', got {e['source']}"
        )

    def test_member_call_not_extracted(self) -> None:
        """$obj->method() member calls → NOT emitted (bare only)."""
        # The fixture has no member calls; this checks no spurious edges are added.
        root = _get_php_root()
        edges = extract_edges(root, "php", SAMPLE_PHP)
        # No method is named 'doSomething' or similar in the fixture.
        # Verifying no member call slips through.
        call_targets = {e["target"] for e in edges if e["kind"] == "call"}
        # All call targets should be bare function names (not method chains)
        for target in call_targets:
            assert "->" not in target, f"Member call target leaked: {target}"

    def test_edges_have_confidence_field(self) -> None:
        """All edges carry a confidence field."""
        root = _get_php_root()
        edges = extract_edges(root, "php", SAMPLE_PHP)
        for e in edges:
            assert e["confidence"] in ("EXTRACTED", "INFERRED", "AMBIGUOUS")


# ── R5: Ruby comments ──────────────────────────────────────────────────────────


class TestRubyComments:
    """R5: Ruby semantic comment extraction from # comments."""

    def test_why_marker_extracted(self) -> None:
        """# WHY: ... → Comment with marker='WHY'."""
        root = _get_rb_root()
        comments = extract_comments(root, "ruby", SAMPLE_RB)
        whys = [c for c in comments if c["marker"] == "WHY"]
        assert len(whys) > 0, "No WHY marker found in Ruby fixture"

    def test_hack_marker_extracted(self) -> None:
        """# HACK: ... → Comment with marker='HACK'."""
        root = _get_rb_root()
        comments = extract_comments(root, "ruby", SAMPLE_RB)
        hacks = [c for c in comments if c["marker"] == "HACK"]
        assert len(hacks) > 0, "No HACK marker found in Ruby fixture"

    def test_note_marker_extracted(self) -> None:
        """# NOTE: ... → Comment with marker='NOTE'."""
        root = _get_rb_root()
        comments = extract_comments(root, "ruby", SAMPLE_RB)
        notes = [c for c in comments if c["marker"] == "NOTE"]
        assert len(notes) > 0, "No NOTE marker found in Ruby fixture"

    def test_plain_comment_not_extracted(self) -> None:
        """'# A person domain object' (plain) → not extracted as a semantic comment."""
        root = _get_rb_root()
        comments = extract_comments(root, "ruby", SAMPLE_RB)
        texts = {c["text"] for c in comments}
        assert not any("person domain object" in t for t in texts)

    def test_comment_has_correct_fields(self) -> None:
        """Each extracted comment has marker, text, and line fields."""
        root = _get_rb_root()
        comments = extract_comments(root, "ruby", SAMPLE_RB)
        valid_markers = {"WHY", "HACK", "NOTE", "TODO", "FIXME"}
        for c in comments:
            assert c["marker"] in valid_markers
            assert isinstance(c["line"], int)
            assert c["line"] >= 1

    def test_returns_list_not_none(self) -> None:
        """extract_comments always returns a list, never raises."""
        root = _get_rb_root()
        result = extract_comments(root, "ruby", SAMPLE_RB)
        assert isinstance(result, list)


# ── R6: PHP comments ───────────────────────────────────────────────────────────


class TestPhpComments:
    """R6: PHP semantic comment extraction from // , # , and /** */ comments."""

    def test_why_in_phpdoc_extracted(self) -> None:
        """/** WHY: ... */ inside phpdoc → Comment with marker='WHY'."""
        root = _get_php_root()
        comments = extract_comments(root, "php", SAMPLE_PHP)
        whys = [c for c in comments if c["marker"] == "WHY"]
        assert len(whys) > 0, "No WHY marker found in PHP fixture"

    def test_hack_in_line_comment_extracted(self) -> None:
        """// HACK: ... → Comment with marker='HACK'."""
        root = _get_php_root()
        comments = extract_comments(root, "php", SAMPLE_PHP)
        hacks = [c for c in comments if c["marker"] == "HACK"]
        assert len(hacks) > 0, "No HACK marker found in PHP fixture"

    def test_note_in_line_comment_extracted(self) -> None:
        """// NOTE: ... → Comment with marker='NOTE'."""
        root = _get_php_root()
        comments = extract_comments(root, "php", SAMPLE_PHP)
        notes = [c for c in comments if c["marker"] == "NOTE"]
        assert len(notes) > 0, "No NOTE marker found in PHP fixture"

    def test_plain_comment_not_extracted(self) -> None:
        """/** List all users. */ plain phpdoc → not extracted as semantic marker."""
        root = _get_php_root()
        comments = extract_comments(root, "php", SAMPLE_PHP)
        texts = {c["text"] for c in comments}
        assert not any("List all users" in t for t in texts)

    def test_comment_fields_valid(self) -> None:
        """All extracted comments have valid marker, text, and line fields."""
        root = _get_php_root()
        comments = extract_comments(root, "php", SAMPLE_PHP)
        valid_markers = {"WHY", "HACK", "NOTE", "TODO", "FIXME"}
        for c in comments:
            assert c["marker"] in valid_markers
            assert isinstance(c["line"], int)
            assert c["line"] >= 1

    def test_returns_list_not_none(self) -> None:
        """extract_comments always returns a list, never raises."""
        root = _get_php_root()
        result = extract_comments(root, "php", SAMPLE_PHP)
        assert isinstance(result, list)


# ── R7: parser ────────────────────────────────────────────────────────────────


class TestRubyPhpParser:
    """R7: parse_ruby and parse_php behave like the existing parsers."""

    def test_parse_ruby_valid_returns_node(self) -> None:
        """parse_ruby(sample.rb) returns a non-None AST root node."""
        from seam.indexer.parser import parse_ruby

        node = parse_ruby(SAMPLE_RB)
        assert node is not None

    def test_parse_ruby_node_has_children(self) -> None:
        """Root from sample.rb has at least one child."""
        from seam.indexer.parser import parse_ruby

        node = parse_ruby(SAMPLE_RB)
        assert node is not None
        assert len(node.children) > 0  # type: ignore[union-attr]

    def test_parse_ruby_binary_returns_none(self, tmp_path: Path) -> None:
        """Binary .rb file → None (not raised)."""
        from seam.indexer.parser import parse_ruby

        binary_file = tmp_path / "binary.rb"
        binary_file.write_bytes(b"class Foo\x00end")
        node = parse_ruby(binary_file)
        assert node is None

    def test_parse_ruby_missing_returns_none(self) -> None:
        """Non-existent .rb path → None."""
        from seam.indexer.parser import parse_ruby

        node = parse_ruby(Path("/nonexistent/path/file.rb"))
        assert node is None

    def test_parse_php_valid_returns_node(self) -> None:
        """parse_php(sample.php) returns a non-None AST root node."""
        from seam.indexer.parser import parse_php

        node = parse_php(SAMPLE_PHP)
        assert node is not None

    def test_parse_php_node_has_children(self) -> None:
        """Root from sample.php has at least one child."""
        from seam.indexer.parser import parse_php

        node = parse_php(SAMPLE_PHP)
        assert node is not None
        assert len(node.children) > 0  # type: ignore[union-attr]

    def test_parse_php_binary_returns_none(self, tmp_path: Path) -> None:
        """Binary .php file → None."""
        from seam.indexer.parser import parse_php

        binary_file = tmp_path / "binary.php"
        binary_file.write_bytes(b"<?php class Foo {}\x00")
        node = parse_php(binary_file)
        assert node is None

    def test_parse_php_missing_returns_none(self) -> None:
        """Non-existent .php path → None."""
        from seam.indexer.parser import parse_php

        node = parse_php(Path("/nonexistent/path/file.php"))
        assert node is None


# ── R8: pipeline ──────────────────────────────────────────────────────────────


class TestRubyPhpPipeline:
    """R8: _dispatch_parser routes .rb/.php; index_one_file indexes the fixtures."""

    def test_dispatch_parser_ruby(self, tmp_path: Path) -> None:
        """_dispatch_parser routes language='ruby' to parse_ruby."""
        from seam.indexer.pipeline import _dispatch_parser

        rb_file = tmp_path / "test.rb"
        rb_file.write_text("class Foo; end\n")
        result = _dispatch_parser(rb_file, "ruby")
        assert result is not None, "_dispatch_parser returned None for Ruby source"

    def test_dispatch_parser_php(self, tmp_path: Path) -> None:
        """_dispatch_parser routes language='php' to parse_php."""
        from seam.indexer.pipeline import _dispatch_parser

        php_file = tmp_path / "test.php"
        php_file.write_text("<?php\nfunction foo() {}\n")
        result = _dispatch_parser(php_file, "php")
        assert result is not None, "_dispatch_parser returned None for PHP source"

    def test_language_map_has_ruby(self) -> None:
        """config.SEAM_LANGUAGE_MAP includes '.rb' → 'ruby'."""
        import seam.config as config

        assert ".rb" in config.SEAM_LANGUAGE_MAP
        assert config.SEAM_LANGUAGE_MAP[".rb"] == "ruby"

    def test_language_map_has_php(self) -> None:
        """config.SEAM_LANGUAGE_MAP includes '.php' → 'php'."""
        import seam.config as config

        assert ".php" in config.SEAM_LANGUAGE_MAP
        assert config.SEAM_LANGUAGE_MAP[".php"] == "php"

    def test_index_one_file_ruby_fixture(self, tmp_path: Path) -> None:
        """index_one_file on sample.rb returns (symbols>0, edges>=0)."""
        from seam.indexer.db import init_db
        from seam.indexer.pipeline import index_one_file

        db_path = tmp_path / "seam.db"
        conn = init_db(db_path)
        result = index_one_file(conn, SAMPLE_RB)
        conn.close()
        assert result is not None, "index_one_file returned None for sample.rb"
        sym_count, _edge_count = result
        assert sym_count > 0, f"Expected symbols > 0 for sample.rb, got {sym_count}"

    def test_index_one_file_php_fixture(self, tmp_path: Path) -> None:
        """index_one_file on sample.php returns (symbols>0, edges>=0)."""
        from seam.indexer.db import init_db
        from seam.indexer.pipeline import index_one_file

        db_path = tmp_path / "seam.db"
        conn = init_db(db_path)
        result = index_one_file(conn, SAMPLE_PHP)
        conn.close()
        assert result is not None, "index_one_file returned None for sample.php"
        sym_count, _edge_count = result
        assert sym_count > 0, f"Expected symbols > 0 for sample.php, got {sym_count}"

    def test_walk_project_finds_ruby_files(self, tmp_path: Path) -> None:
        """walk_project includes .rb files."""
        from seam.indexer.pipeline import walk_project

        rb_file = tmp_path / "app.rb"
        rb_file.write_text("class App; end\n")
        files = walk_project(tmp_path)
        paths = {f.name for f in files}
        assert "app.rb" in paths, f"app.rb not found in walk_project output: {paths}"

    def test_walk_project_finds_php_files(self, tmp_path: Path) -> None:
        """walk_project includes .php files."""
        from seam.indexer.pipeline import walk_project

        php_file = tmp_path / "index.php"
        php_file.write_text("<?php\necho 'hello';\n")
        files = walk_project(tmp_path)
        paths = {f.name for f in files}
        assert "index.php" in paths, f"index.php not found in walk_project output: {paths}"


# ── R9: signatures ─────────────────────────────────────────────────────────────


class TestRubyPhpSignatures:
    """R9: Phase 4 enrichment fields for Ruby and PHP symbols."""

    def test_ruby_method_signature_present(self) -> None:
        """Ruby method node → signature is not None."""
        from seam.indexer.parser import parse_ruby

        root = parse_ruby(SAMPLE_RB)
        assert root is not None
        symbols = extract_symbols(root, "ruby", SAMPLE_RB)
        sym = _sym(symbols, "Person.greet")
        assert sym is not None
        assert sym["signature"] is not None, "Expected non-None signature for Person.greet"

    def test_ruby_class_signature_present(self) -> None:
        """Ruby class node → signature is not None."""
        root = _get_rb_root()
        symbols = extract_symbols(root, "ruby", SAMPLE_RB)
        sym = _sym(symbols, "Person")
        assert sym is not None
        assert sym["signature"] is not None

    def test_php_public_method_visibility(self) -> None:
        """PHP public method → visibility='public', is_exported=True."""
        root = _get_php_root()
        symbols = extract_symbols(root, "php", SAMPLE_PHP)
        sym = _sym(symbols, "UserController.index")
        assert sym is not None
        assert sym["visibility"] == "public"
        assert sym["is_exported"] is True

    def test_php_protected_method_visibility(self) -> None:
        """PHP protected method → visibility='protected'."""
        root = _get_php_root()
        symbols = extract_symbols(root, "php", SAMPLE_PHP)
        sym = _sym(symbols, "UserController.findUser")
        assert sym is not None
        assert sym["visibility"] == "protected"

    def test_php_method_with_attribute_has_decorators(self) -> None:
        """PHP #[Route('/users')] attribute → decorators list non-empty."""
        root = _get_php_root()
        symbols = extract_symbols(root, "php", SAMPLE_PHP)
        sym = _sym(symbols, "UserController.index")
        assert sym is not None
        assert len(sym["decorators"]) > 0, (
            "Expected decorators for #[Route('/users')], got empty list"
        )

    def test_php_method_signature_present(self) -> None:
        """PHP method → signature is not None."""
        root = _get_php_root()
        symbols = extract_symbols(root, "php", SAMPLE_PHP)
        sym = _sym(symbols, "getUsers")
        assert sym is not None
        assert sym["signature"] is not None


# ── R10: imports ───────────────────────────────────────────────────────────────


class TestRubyPhpImports:
    """R10: Import mappings extracted for Ruby and PHP."""

    def test_ruby_require_binding_extracted(self) -> None:
        """require 'json' → ImportMapping with local_name='json'."""
        from seam.analysis.imports import extract_import_mappings
        from seam.indexer.parser import parse_ruby

        root = parse_ruby(SAMPLE_RB)
        assert root is not None
        mappings = extract_import_mappings(root, SAMPLE_RB, "ruby")
        names = {m["local_name"] for m in mappings}
        assert "json" in names, f"Expected 'json' mapping, got: {names}"

    def test_ruby_require_relative_binding_extracted(self) -> None:
        """require_relative './helper' → ImportMapping with local_name='helper'."""
        from seam.analysis.imports import extract_import_mappings
        from seam.indexer.parser import parse_ruby

        root = parse_ruby(SAMPLE_RB)
        assert root is not None
        mappings = extract_import_mappings(root, SAMPLE_RB, "ruby")
        names = {m["local_name"] for m in mappings}
        assert "helper" in names, f"Expected 'helper' mapping, got: {names}"

    def test_php_use_binding_extracted(self) -> None:
        """use App\\Models\\User; → ImportMapping with local_name='User'."""
        from seam.analysis.imports import extract_import_mappings
        from seam.indexer.parser import parse_php

        root = parse_php(SAMPLE_PHP)
        assert root is not None
        mappings = extract_import_mappings(root, SAMPLE_PHP, "php")
        names = {m["local_name"] for m in mappings}
        assert "User" in names, f"Expected 'User' mapping, got: {names}"

    def test_php_second_use_binding_extracted(self) -> None:
        """use App\\Services\\Logger; → ImportMapping with local_name='Logger'."""
        from seam.analysis.imports import extract_import_mappings
        from seam.indexer.parser import parse_php

        root = parse_php(SAMPLE_PHP)
        assert root is not None
        mappings = extract_import_mappings(root, SAMPLE_PHP, "php")
        names = {m["local_name"] for m in mappings}
        assert "Logger" in names, f"Expected 'Logger' mapping, got: {names}"


# ── R11: builtins ──────────────────────────────────────────────────────────────


class TestRubyPhpBuiltins:
    """R11: is_builtin correctly identifies Ruby and PHP builtins."""

    def test_ruby_builtin_true(self) -> None:
        """'puts' is a known Ruby builtin → is_builtin returns True."""
        from seam.analysis.builtins import is_builtin

        assert is_builtin("puts", "ruby") is True

    def test_ruby_require_is_builtin(self) -> None:
        """'require' is a core Ruby global → is_builtin returns True."""
        from seam.analysis.builtins import is_builtin

        assert is_builtin("require", "ruby") is True

    def test_ruby_repo_name_not_builtin(self) -> None:
        """'PersonRepository' is a user-defined class → is_builtin returns False."""
        from seam.analysis.builtins import is_builtin

        assert is_builtin("PersonRepository", "ruby") is False

    def test_php_builtin_true(self) -> None:
        """'echo' is a known PHP builtin → is_builtin returns True."""
        from seam.analysis.builtins import is_builtin

        assert is_builtin("echo", "php") is True

    def test_php_count_is_builtin(self) -> None:
        """'count' is a core PHP function → is_builtin returns True."""
        from seam.analysis.builtins import is_builtin

        assert is_builtin("count", "php") is True

    def test_php_repo_name_not_builtin(self) -> None:
        """'UserRepository' is a user-defined class → is_builtin returns False."""
        from seam.analysis.builtins import is_builtin

        assert is_builtin("UserRepository", "php") is False

    def test_ruby_builtin_not_found_in_php(self) -> None:
        """'puts' (Ruby builtin) → is_builtin('puts', 'php') False (lang-scoped)."""
        from seam.analysis.builtins import is_builtin

        assert is_builtin("puts", "php") is False


# ── R12: PHP regression fixes (Phase 9 code review) ───────────────────────────


class TestPhpRegressions:
    """R12: Regression tests for PHP extraction bugs found in Phase 9 code review.

    Bug 7  — Grouped use: use App\\{Foo, Bar} emits ZERO edges/mappings.
    Bug 8  — Aliased use: local_name should be the alias, exported_name the real name.
    Bug 9  — Enum methods dropped: method_declaration inside enum body not extracted.
    Bug 10 — Dead code: attribute_list prev_sibling branch in phpdoc lookup removed;
             phpdoc on attributed enum must still resolve via real adjacent comment.
    """

    # -- Bug 7: grouped use edges ---

    def test_grouped_use_foo_import_edge(self) -> None:
        """use App\\{Foo, Bar} → import edge with target='Foo' (grouped use clause)."""
        root = _get_php_root()
        edges = extract_edges(root, "php", SAMPLE_PHP)
        targets = {e["target"] for e in edges if e["kind"] == "import"}
        assert "Repository" in targets, (
            f"Expected 'Repository' import from grouped use, got: {targets}"
        )

    def test_grouped_use_bar_import_edge(self) -> None:
        """use App\\{Repository, Cacheable} → import edge with target='Cacheable'."""
        root = _get_php_root()
        edges = extract_edges(root, "php", SAMPLE_PHP)
        targets = {e["target"] for e in edges if e["kind"] == "import"}
        assert "Cacheable" in targets, (
            f"Expected 'Cacheable' import from grouped use, got: {targets}"
        )

    def test_grouped_use_import_mapping_foo(self) -> None:
        """use App\\{Repository, Cacheable} → ImportMapping local_name='Repository'."""
        from seam.analysis.imports import extract_import_mappings
        from seam.indexer.parser import parse_php

        root = parse_php(SAMPLE_PHP)
        assert root is not None
        mappings = extract_import_mappings(root, SAMPLE_PHP, "php")
        names = {m["local_name"] for m in mappings}
        assert "Repository" in names, f"Expected 'Repository' mapping, got: {names}"

    def test_grouped_use_import_mapping_bar(self) -> None:
        """use App\\{Repository, Cacheable} → ImportMapping local_name='Cacheable'."""
        from seam.analysis.imports import extract_import_mappings
        from seam.indexer.parser import parse_php

        root = parse_php(SAMPLE_PHP)
        assert root is not None
        mappings = extract_import_mappings(root, SAMPLE_PHP, "php")
        names = {m["local_name"] for m in mappings}
        assert "Cacheable" in names, f"Expected 'Cacheable' mapping, got: {names}"

    # -- Bug 8: aliased use import mapping ---

    def test_aliased_use_local_name_is_alias(self) -> None:
        """use App\\Support\\Collection as Col → local_name='Col' (the alias)."""
        from seam.analysis.imports import extract_import_mappings
        from seam.indexer.parser import parse_php

        root = parse_php(SAMPLE_PHP)
        assert root is not None
        mappings = extract_import_mappings(root, SAMPLE_PHP, "php")
        col_mapping = next((m for m in mappings if m["local_name"] == "Col"), None)
        assert col_mapping is not None, (
            f"No mapping with local_name='Col' found. Mappings: {[(m['local_name'], m['exported_name']) for m in mappings]}"
        )
        assert col_mapping["exported_name"] == "Collection", (
            f"Expected exported_name='Collection', got {col_mapping['exported_name']!r}"
        )

    def test_aliased_use_no_spurious_real_name_mapping(self) -> None:
        """use App\\Support\\Collection as Col → only ONE mapping (Col), not also 'Collection'."""
        from seam.analysis.imports import extract_import_mappings
        from seam.indexer.parser import parse_php

        root = parse_php(SAMPLE_PHP)
        assert root is not None
        mappings = extract_import_mappings(root, SAMPLE_PHP, "php")
        # Should have exactly one mapping related to this use statement, with local_name='Col'
        col_mappings = [m for m in mappings if m["exported_name"] == "Collection"]
        assert len(col_mappings) == 1, (
            f"Expected exactly 1 mapping for Collection, got {len(col_mappings)}: "
            f"{[(m['local_name'], m['exported_name']) for m in col_mappings]}"
        )
        assert col_mappings[0]["local_name"] == "Col"

    def test_aliased_use_graph_edge_targets_real_name(self) -> None:
        """use App\\Support\\Collection as Col → graph import edge targets 'Collection' (real name).

        WHY: the edge targets the REAL exported symbol so it links to the declaration;
        the local alias 'Col' is tracked separately in the import mapping.
        """
        root = _get_php_root()
        edges = extract_edges(root, "php", SAMPLE_PHP)
        targets = {e["target"] for e in edges if e["kind"] == "import"}
        assert "Collection" in targets, (
            f"Expected 'Collection' import edge for aliased use, got: {targets}"
        )

    # -- Bug 9: enum methods ---

    def test_enum_method_extracted(self) -> None:
        """method_declaration inside enum Suit → symbol 'Suit.color' kind='method'."""
        root = _get_php_root()
        symbols = extract_symbols(root, "php", SAMPLE_PHP)
        sym = _sym(symbols, "Suit.color")
        assert sym is not None, (
            f"'Suit.color' not found in enum body. Symbols: {[s['name'] for s in symbols]}"
        )
        assert sym["kind"] == "method"

    def test_enum_itself_still_type(self) -> None:
        """enum Suit itself is still emitted as kind='type' after method recursion fix."""
        root = _get_php_root()
        symbols = extract_symbols(root, "php", SAMPLE_PHP)
        sym = _sym(symbols, "Suit")
        assert sym is not None, "'Suit' enum not found"
        assert sym["kind"] == "type"

    # -- Bug 10: dead attribute_list prev_sibling branch removed ---

    def test_phpdoc_on_attributed_enum_resolves(self) -> None:
        """Enum Suit has /** A backed enum... */ phpdoc plus #[Attr] attribute.

        The attribute is a CHILD of the declaration (not prev_sibling), so the phpdoc
        lookup must find the adjacent comment above the enum_declaration node.
        With the dead attribute_list branch removed, the real phpdoc must still resolve.
        """
        root = _get_php_root()
        symbols = extract_symbols(root, "php", SAMPLE_PHP)
        sym = _sym(symbols, "Suit")
        assert sym is not None, "'Suit' not found"
        assert sym["docstring"] is not None, "Expected phpdoc for Suit (/** A backed enum... */)"
        assert "backed enum" in sym["docstring"].lower(), (
            f"Expected 'backed enum' in docstring, got: {sym['docstring']!r}"
        )


# ── R13: Ruby signature regression (Phase 9 code review) ──────────────────────


class TestRubySignatureRegression:
    """R13: Ruby class/module signatures must not include inline comment text.

    Bug 11 — Ruby class/module signatures sweep in inline comment nodes between
    the name and body_statement. The signature collector must skip 'comment' nodes.
    """

    def test_utils_module_signature_no_comment(self) -> None:
        """module Utils # NOTE: ... → signature should be 'module Utils' (no '#' text)."""
        root = _get_rb_root()
        symbols = extract_symbols(root, "ruby", SAMPLE_RB)
        sym = _sym(symbols, "Utils")
        assert sym is not None, "'Utils' module not found"
        sig = sym["signature"]
        assert sig is not None, "Expected non-None signature for Utils"
        assert "#" not in sig, f"Signature contains inline comment text '#': {sig!r}"
        assert "NOTE" not in sig, f"Signature contains comment marker 'NOTE': {sig!r}"

    def test_person_class_signature_no_comment(self) -> None:
        """class Person # Initialize... → signature should be 'class Person' (no '#' text)."""
        root = _get_rb_root()
        symbols = extract_symbols(root, "ruby", SAMPLE_RB)
        sym = _sym(symbols, "Person")
        assert sym is not None, "'Person' not found"
        sig = sym["signature"]
        assert sig is not None, "Expected non-None signature for Person"
        assert "#" not in sig, f"Signature contains inline comment text '#': {sig!r}"

    def test_utils_module_signature_content(self) -> None:
        """Utils signature is exactly 'module Utils' (keyword + name only)."""
        root = _get_rb_root()
        symbols = extract_symbols(root, "ruby", SAMPLE_RB)
        sym = _sym(symbols, "Utils")
        assert sym is not None
        sig = sym["signature"]
        # Normalize whitespace for comparison
        normalized = " ".join(sig.split()) if sig else ""
        assert normalized == "module Utils", f"Expected 'module Utils', got: {sig!r}"
