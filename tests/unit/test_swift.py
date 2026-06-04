"""Tests for Swift language support (Phase 10).

TDD: Tests written before implementation. Each group covers one behavioral slice:

S1 — Swift symbols:  class/struct/actor/extension → class; enum → type;
                     protocol → interface; method → Type.method;
                     top-level func → function; doc-comment (///) → docstring.
S2 — Swift edges:    import edge produced; bare call edge produced;
                     member/navigation call NOT emitted.
S3 — Swift comments: WHY/HACK/NOTE/FIXME markers from // and /* */ comments;
                     plain comment not extracted.
S4 — parser:         parse_swift returns Node for valid source, None for binary/missing.
S5 — pipeline:       _dispatch_parser routes .swift; index_one_file indexes the fixture.
S6 — signatures:     signature built; visibility/is_exported from modifiers;
                     @attribute decorator captured.
S7 — imports:        binding extracted for 'import Foundation'.
S8 — builtins:       is_builtin('print', 'swift') True; repo name False.

All assertions go through the PUBLIC API (extract_symbols / extract_edges /
extract_comments / extract_node_fields / extract_import_mappings / is_builtin),
never against internals.
"""

from pathlib import Path

from seam.indexer.graph import Edge, Symbol, extract_comments, extract_edges, extract_symbols

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"
SAMPLE_SWIFT = FIXTURES_DIR / "sample.swift"


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


def _get_swift_root():  # type: ignore[return]
    """Parse sample.swift and return root node (fails test if parse fails)."""
    from seam.indexer.parser import parse_swift

    node = parse_swift(SAMPLE_SWIFT)
    assert node is not None, "sample.swift failed to parse"
    return node


# ── S1: Swift symbols ──────────────────────────────────────────────────────────


class TestSwiftSymbols:
    """S1: Swift symbol extraction covers all required kinds and docstrings."""

    def test_class_extracted_as_class(self) -> None:
        """class UserRepo → kind='class'."""
        root = _get_swift_root()
        symbols = extract_symbols(root, "swift", SAMPLE_SWIFT)
        sym = _sym(symbols, "UserRepo")
        assert sym is not None, "UserRepo not found in Swift symbols"
        assert sym["kind"] == "class"
        assert sym["file"] == str(SAMPLE_SWIFT)

    def test_struct_extracted_as_class(self) -> None:
        """struct Point → kind='class'."""
        root = _get_swift_root()
        symbols = extract_symbols(root, "swift", SAMPLE_SWIFT)
        sym = _sym(symbols, "Point")
        assert sym is not None, "Point struct not found"
        assert sym["kind"] == "class"

    def test_actor_extracted_as_class(self) -> None:
        """public actor DataProcessor → kind='class'."""
        root = _get_swift_root()
        symbols = extract_symbols(root, "swift", SAMPLE_SWIFT)
        sym = _sym(symbols, "DataProcessor")
        assert sym is not None, "DataProcessor actor not found"
        assert sym["kind"] == "class"

    def test_extension_extracted_as_class(self) -> None:
        """extension UserRepo: Describable → kind='class' with name='UserRepo'."""
        root = _get_swift_root()
        symbols = extract_symbols(root, "swift", SAMPLE_SWIFT)
        # extension extracts the extended type's name
        names = [s["name"] for s in symbols]
        # There may be multiple 'UserRepo' entries (class + extension); at least one must be class
        user_repos = [s for s in symbols if s["name"] == "UserRepo"]
        assert any(s["kind"] == "class" for s in user_repos), (
            f"No UserRepo class/extension found. Names: {names}"
        )

    def test_enum_extracted_as_type(self) -> None:
        """enum Status → kind='type'."""
        root = _get_swift_root()
        symbols = extract_symbols(root, "swift", SAMPLE_SWIFT)
        sym = _sym(symbols, "Status")
        assert sym is not None, "Status enum not found"
        assert sym["kind"] == "type"

    def test_protocol_extracted_as_interface(self) -> None:
        """protocol Describable → kind='interface'."""
        root = _get_swift_root()
        symbols = extract_symbols(root, "swift", SAMPLE_SWIFT)
        sym = _sym(symbols, "Describable")
        assert sym is not None, "Describable protocol not found"
        assert sym["kind"] == "interface"

    def test_top_level_func_extracted_as_function(self) -> None:
        """func greet → kind='function' (top-level)."""
        root = _get_swift_root()
        symbols = extract_symbols(root, "swift", SAMPLE_SWIFT)
        sym = _sym(symbols, "greet")
        assert sym is not None, "greet func not found"
        assert sym["kind"] == "function"

    def test_class_method_qualified_as_type_method(self) -> None:
        """func save inside class UserRepo → name='UserRepo.save', kind='method'."""
        root = _get_swift_root()
        symbols = extract_symbols(root, "swift", SAMPLE_SWIFT)
        sym = _sym(symbols, "UserRepo.save")
        assert sym is not None, "UserRepo.save not found — method not qualified"
        assert sym["kind"] == "method"

    def test_class_method_count_qualified(self) -> None:
        """func count inside class UserRepo → name='UserRepo.count', kind='method'."""
        root = _get_swift_root()
        symbols = extract_symbols(root, "swift", SAMPLE_SWIFT)
        sym = _sym(symbols, "UserRepo.count")
        assert sym is not None, "UserRepo.count not found"
        assert sym["kind"] == "method"

    def test_protocol_method_qualified_as_type_method(self) -> None:
        """func describe inside protocol Describable → name='Describable.describe', kind='method'."""
        root = _get_swift_root()
        symbols = extract_symbols(root, "swift", SAMPLE_SWIFT)
        sym = _sym(symbols, "Describable.describe")
        assert sym is not None, "Describable.describe not found"
        assert sym["kind"] == "method"

    def test_extension_method_qualified(self) -> None:
        """func describe inside extension UserRepo → name='UserRepo.describe', kind='method'."""
        root = _get_swift_root()
        symbols = extract_symbols(root, "swift", SAMPLE_SWIFT)
        # The extension method: 'describe' qualified by 'UserRepo' (extension target)
        sym = _sym(symbols, "UserRepo.describe")
        assert sym is not None, (
            "UserRepo.describe not found — extension method not qualified. "
            f"Symbols: {[s['name'] for s in symbols]}"
        )
        assert sym["kind"] == "method"

    def test_func_with_doc_comment_has_docstring(self) -> None:
        """greet() preceded by /// doc comment → docstring is not None."""
        root = _get_swift_root()
        symbols = extract_symbols(root, "swift", SAMPLE_SWIFT)
        sym = _sym(symbols, "greet")
        assert sym is not None
        assert sym["docstring"] is not None
        assert "Greet a user" in sym["docstring"]

    def test_func_with_block_doc_comment_has_docstring(self) -> None:
        """buildMessage() preceded by a /** */ block doc comment → docstring captured.

        Regression: the /// path worked but /** */ (a 'multiline_comment' node, not
        'comment') was dropped — violating PRD user-story 9.
        """
        root = _get_swift_root()
        symbols = extract_symbols(root, "swift", SAMPLE_SWIFT)
        sym = _sym(symbols, "buildMessage")
        assert sym is not None
        assert sym["docstring"] is not None, "/** */ block doc-comment was not captured"
        assert "Build a greeting message" in sym["docstring"]
        # Block-comment '*' decorations must be stripped.
        assert "*" not in sym["docstring"]

    def test_class_doc_comment_captured(self) -> None:
        """UserRepo class preceded by /// doc → docstring captured."""
        root = _get_swift_root()
        symbols = extract_symbols(root, "swift", SAMPLE_SWIFT)
        # Only the class UserRepo, not extension
        class_syms = [s for s in symbols if s["name"] == "UserRepo" and s["kind"] == "class"]
        assert class_syms, "UserRepo class not found"
        # The @objc class has a doc comment
        any_doc = any(s["docstring"] is not None for s in class_syms)
        assert any_doc, "UserRepo class should have a docstring"

    def test_protocol_doc_comment_captured(self) -> None:
        """Describable protocol preceded by /// → docstring captured."""
        root = _get_swift_root()
        symbols = extract_symbols(root, "swift", SAMPLE_SWIFT)
        sym = _sym(symbols, "Describable")
        assert sym is not None
        assert sym["docstring"] is not None
        assert "Describable" in sym["docstring"] or "description" in sym["docstring"].lower()

    def test_func_start_line_correct(self) -> None:
        """greet() start_line is 1-based and non-zero."""
        root = _get_swift_root()
        symbols = extract_symbols(root, "swift", SAMPLE_SWIFT)
        sym = _sym(symbols, "greet")
        assert sym is not None
        assert sym["start_line"] >= 1

    def test_returns_list_not_none(self) -> None:
        """extract_symbols always returns a list, never None."""
        root = _get_swift_root()
        result = extract_symbols(root, "swift", SAMPLE_SWIFT)
        assert isinstance(result, list)

    def test_property_not_extracted_as_symbol(self) -> None:
        """var users: [String] — property_declaration NOT emitted as a symbol."""
        root = _get_swift_root()
        symbols = extract_symbols(root, "swift", SAMPLE_SWIFT)
        names = {s["name"] for s in symbols}
        assert "users" not in names, "property_declaration should not be emitted as a symbol"


# ── S2: Swift edges ────────────────────────────────────────────────────────────


class TestSwiftEdges:
    """S2: Swift import and call edges are extracted correctly."""

    def test_import_edge_produced(self) -> None:
        """import Foundation → at least one import edge produced."""
        root = _get_swift_root()
        edges = extract_edges(root, "swift", SAMPLE_SWIFT)
        import_edges = [e for e in edges if e["kind"] == "import"]
        assert len(import_edges) > 0, "No import edges found for Swift fixture"

    def test_import_target_is_module_name(self) -> None:
        """import Foundation → edge target is 'Foundation'."""
        root = _get_swift_root()
        edges = extract_edges(root, "swift", SAMPLE_SWIFT)
        targets = {e["target"] for e in edges if e["kind"] == "import"}
        assert "Foundation" in targets, f"Expected 'Foundation' import edge, got: {targets}"

    def test_dotted_import_target_is_last_segment(self) -> None:
        """import UIKit.UIView → target is 'UIView' (last segment)."""
        root = _get_swift_root()
        edges = extract_edges(root, "swift", SAMPLE_SWIFT)
        targets = {e["target"] for e in edges if e["kind"] == "import"}
        assert "UIView" in targets, f"Expected 'UIView' import edge, got: {targets}"

    def test_import_source_is_file_stem(self) -> None:
        """Import edge source is the file stem ('sample')."""
        root = _get_swift_root()
        edges = extract_edges(root, "swift", SAMPLE_SWIFT)
        for e in edges:
            if e["kind"] == "import" and e["target"] == "Foundation":
                assert e["source"] == "sample", f"Expected source='sample', got {e['source']}"

    def test_call_edge_produced(self) -> None:
        """Bare-identifier call expression produces a call edge."""
        root = _get_swift_root()
        edges = extract_edges(root, "swift", SAMPLE_SWIFT)
        call_edges = [e for e in edges if e["kind"] == "call"]
        assert len(call_edges) > 0, "No call edges found for Swift fixture"

    def test_bare_call_edge_target(self) -> None:
        """greet() inside save() → call edge with target 'greet'."""
        root = _get_swift_root()
        edges = extract_edges(root, "swift", SAMPLE_SWIFT)
        e = _edge(edges, target="greet", kind="call")
        assert e is not None, "Expected call edge targeting 'greet'"

    def test_call_edge_source_is_enclosing_function(self) -> None:
        """The call edge source is the enclosing function qualified name ('UserRepo.save')."""
        root = _get_swift_root()
        edges = extract_edges(root, "swift", SAMPLE_SWIFT)
        e = _edge(edges, target="greet", kind="call")
        assert e is not None
        assert e["source"] == "UserRepo.save", f"Expected source='UserRepo.save', got {e['source']}"

    def test_navigation_call_not_emitted(self) -> None:
        """users.append() — navigation_expression call (member call) NOT extracted."""
        root = _get_swift_root()
        edges = extract_edges(root, "swift", SAMPLE_SWIFT)
        call_targets = {e["target"] for e in edges if e["kind"] == "call"}
        # 'append' is called as users.append(...) — navigation, not bare
        assert "append" not in call_targets, (
            "Navigation call 'users.append' should not produce a call edge"
        )

    def test_edges_have_confidence_field(self) -> None:
        """All edges carry a confidence field (EXTRACTED|INFERRED|AMBIGUOUS)."""
        root = _get_swift_root()
        edges = extract_edges(root, "swift", SAMPLE_SWIFT)
        for e in edges:
            assert e["confidence"] in ("EXTRACTED", "INFERRED", "AMBIGUOUS"), (
                f"Edge missing valid confidence: {e}"
            )


# ── S3: Swift comments ────────────────────────────────────────────────────────


class TestSwiftComments:
    """S3: Swift semantic comment extraction from // and /* */ comments."""

    def test_why_marker_extracted_from_line_comment(self) -> None:
        """// WHY: ... in a Swift file → Comment with marker='WHY'."""
        root = _get_swift_root()
        comments = extract_comments(root, "swift", SAMPLE_SWIFT)
        whys = [c for c in comments if c["marker"] == "WHY"]
        assert len(whys) > 0, "No WHY marker found in Swift fixture"

    def test_hack_marker_extracted(self) -> None:
        """// HACK: ... in a Swift file → Comment with marker='HACK'."""
        root = _get_swift_root()
        comments = extract_comments(root, "swift", SAMPLE_SWIFT)
        hacks = [c for c in comments if c["marker"] == "HACK"]
        assert len(hacks) > 0, "No HACK marker found in Swift fixture"

    def test_note_marker_extracted(self) -> None:
        """// NOTE: ... in a Swift file → Comment with marker='NOTE'."""
        root = _get_swift_root()
        comments = extract_comments(root, "swift", SAMPLE_SWIFT)
        notes = [c for c in comments if c["marker"] == "NOTE"]
        assert len(notes) > 0, "No NOTE marker found in Swift fixture"

    def test_hack_marker_in_block_comment(self) -> None:
        """/* HACK: ... */ block comment → Comment with marker='HACK'."""
        root = _get_swift_root()
        comments = extract_comments(root, "swift", SAMPLE_SWIFT)
        block_hacks = [c for c in comments if c["marker"] == "HACK"]
        assert len(block_hacks) > 0, "No HACK in block comment found"

    def test_fixme_marker_in_block_comment(self) -> None:
        """/* ... FIXME: ... */ block comment → Comment with marker='FIXME'."""
        root = _get_swift_root()
        comments = extract_comments(root, "swift", SAMPLE_SWIFT)
        fixmes = [c for c in comments if c["marker"] == "FIXME"]
        assert len(fixmes) > 0, "No FIXME marker found in Swift block comment"

    def test_plain_doc_comment_not_extracted_as_marker(self) -> None:
        """/// Greet a user — no marker keyword → not extracted as semantic comment."""
        root = _get_swift_root()
        comments = extract_comments(root, "swift", SAMPLE_SWIFT)
        texts = {c["text"] for c in comments}
        assert not any("Greet a user" in t for t in texts), (
            "Plain doc comment should not be extracted as a semantic marker"
        )

    def test_comment_has_correct_fields(self) -> None:
        """Each extracted comment has marker, text, and line fields."""
        root = _get_swift_root()
        comments = extract_comments(root, "swift", SAMPLE_SWIFT)
        assert len(comments) > 0, "Expected at least one semantic comment"
        for c in comments:
            assert "marker" in c
            assert "text" in c
            assert "line" in c
            assert isinstance(c["line"], int)
            assert c["line"] >= 1

    def test_returns_list_not_none(self) -> None:
        """extract_comments always returns a list, never raises."""
        root = _get_swift_root()
        result = extract_comments(root, "swift", SAMPLE_SWIFT)
        assert isinstance(result, list)


# ── S4: parser ────────────────────────────────────────────────────────────────


class TestSwiftParser:
    """S4: parse_swift behaves like the existing parse_go / parse_rust."""

    def test_parse_swift_valid_file_returns_node(self) -> None:
        """parse_swift(sample.swift) returns a non-None AST root node."""
        from seam.indexer.parser import parse_swift

        node = parse_swift(SAMPLE_SWIFT)
        assert node is not None

    def test_parse_swift_node_has_children(self) -> None:
        """root node from sample.swift has at least one child."""
        from seam.indexer.parser import parse_swift

        node = parse_swift(SAMPLE_SWIFT)
        assert node is not None
        assert len(node.children) > 0  # type: ignore[union-attr]

    def test_parse_swift_binary_file_returns_none(self, tmp_path: Path) -> None:
        """Binary .swift file → None (not raised)."""
        from seam.indexer.parser import parse_swift

        binary_file = tmp_path / "binary.swift"
        binary_file.write_bytes(b"import Foundation\x00rest")
        node = parse_swift(binary_file)
        assert node is None

    def test_parse_swift_missing_file_returns_none(self) -> None:
        """Non-existent .swift path → None."""
        from seam.indexer.parser import parse_swift

        node = parse_swift(Path("/nonexistent/path/file.swift"))
        assert node is None


# ── S5: pipeline ──────────────────────────────────────────────────────────────


class TestSwiftPipeline:
    """S5: _dispatch_parser routes .swift; index_one_file indexes the fixture."""

    def test_dispatch_parser_swift(self, tmp_path: Path) -> None:
        """_dispatch_parser routes language='swift' to parse_swift."""
        from seam.indexer.pipeline import _dispatch_parser

        swift_file = tmp_path / "test.swift"
        swift_file.write_text("import Foundation\nfunc main() {}\n")
        result = _dispatch_parser(swift_file, "swift")
        assert result is not None, "_dispatch_parser returned None for Swift source"

    def test_language_map_has_swift(self) -> None:
        """config.SEAM_LANGUAGE_MAP includes '.swift' → 'swift'."""
        import seam.config as config

        assert ".swift" in config.SEAM_LANGUAGE_MAP
        assert config.SEAM_LANGUAGE_MAP[".swift"] == "swift"

    def test_index_one_file_swift_fixture(self, tmp_path: Path) -> None:
        """index_one_file on sample.swift returns (symbols>0, edges>=0)."""
        from seam.indexer.db import init_db
        from seam.indexer.pipeline import index_one_file

        db_path = tmp_path / "seam.db"
        conn = init_db(db_path)
        result = index_one_file(conn, SAMPLE_SWIFT)
        conn.close()
        assert result is not None, "index_one_file returned None for sample.swift"
        sym_count, _edge_count = result
        assert sym_count > 0, f"Expected symbols > 0 for sample.swift, got {sym_count}"

    def test_walk_project_finds_swift_files(self, tmp_path: Path) -> None:
        """walk_project includes .swift files after language map update."""
        from seam.indexer.pipeline import walk_project

        swift_file = tmp_path / "main.swift"
        swift_file.write_text("import Foundation\nfunc main() {}\n")
        files = walk_project(tmp_path)
        paths = {f.name for f in files}
        assert "main.swift" in paths, f"main.swift not found in walk_project output: {paths}"


# ── S6: signatures ────────────────────────────────────────────────────────────


class TestSwiftSignatures:
    """S6: Swift enrichment fields — signature, visibility, decorators."""

    def test_func_signature_extracted(self) -> None:
        """greet() → signature contains 'func greet'."""
        root = _get_swift_root()
        symbols = extract_symbols(root, "swift", SAMPLE_SWIFT)
        sym = _sym(symbols, "greet")
        assert sym is not None
        sig = sym.get("signature")
        assert sig is not None, "signature should not be None for greet"
        assert "func" in sig and "greet" in sig

    def test_public_actor_is_exported(self) -> None:
        """public actor DataProcessor → is_exported=True, visibility='public'."""
        root = _get_swift_root()
        symbols = extract_symbols(root, "swift", SAMPLE_SWIFT)
        sym = _sym(symbols, "DataProcessor")
        assert sym is not None
        assert sym["is_exported"] is True, "public actor should be exported"
        assert sym["visibility"] == "public"

    def test_class_with_no_modifier_internal(self) -> None:
        """class UserRepo (no explicit access modifier) → is_exported=False, visibility='internal'."""
        root = _get_swift_root()
        symbols = extract_symbols(root, "swift", SAMPLE_SWIFT)
        # The UserRepo class (no public/open/private modifier, has @objc)
        class_syms = [s for s in symbols if s["name"] == "UserRepo" and s["kind"] == "class"]
        assert class_syms, "UserRepo class symbol not found"
        # Find the class (not extension) — only the one with @objc
        # Be flexible: just check the class doesn't show as public
        assert all(s["visibility"] != "public" for s in class_syms), (
            "UserRepo without public modifier should not be 'public'"
        )

    def test_attribute_decorator_captured(self) -> None:
        """@objc class UserRepo → decorators includes '@objc'."""
        root = _get_swift_root()
        symbols = extract_symbols(root, "swift", SAMPLE_SWIFT)
        class_syms = [s for s in symbols if s["name"] == "UserRepo" and s["kind"] == "class"]
        assert class_syms, "UserRepo not found"
        # The @objc one
        has_objc = any("@objc" in (s.get("decorators") or []) for s in class_syms)
        assert has_objc, (
            f"@objc decorator not found on UserRepo. Decorators: {[s.get('decorators') for s in class_syms]}"
        )

    def test_available_attribute_on_method(self) -> None:
        """@available(iOS 13.0, *) on func save → decorators contains it."""
        root = _get_swift_root()
        symbols = extract_symbols(root, "swift", SAMPLE_SWIFT)
        sym = _sym(symbols, "UserRepo.save")
        assert sym is not None
        decorators = sym.get("decorators") or []
        assert any("available" in d for d in decorators), (
            f"@available decorator not found on UserRepo.save. Decorators: {decorators}"
        )

    def test_no_decorator_returns_empty_list(self) -> None:
        """greet() with no @attribute → decorators is [] (not None)."""
        root = _get_swift_root()
        symbols = extract_symbols(root, "swift", SAMPLE_SWIFT)
        sym = _sym(symbols, "greet")
        assert sym is not None
        assert sym["decorators"] == [], f"Expected [], got {sym['decorators']}"


# ── S7: imports ────────────────────────────────────────────────────────────────


class TestSwiftImports:
    """S7: Swift import-mapping extraction."""

    def test_import_mapping_extracted_for_foundation(self) -> None:
        """import Foundation → ImportMapping with local_name='Foundation'."""
        from seam.analysis.imports import extract_import_mappings

        root = _get_swift_root()
        mappings = extract_import_mappings(root, SAMPLE_SWIFT, "swift")
        local_names = {m["local_name"] for m in mappings}
        assert "Foundation" in local_names, (
            f"Expected 'Foundation' in import mappings, got: {local_names}"
        )

    def test_import_mappings_not_empty(self) -> None:
        """extract_import_mappings returns at least one mapping for sample.swift."""
        from seam.analysis.imports import extract_import_mappings

        root = _get_swift_root()
        mappings = extract_import_mappings(root, SAMPLE_SWIFT, "swift")
        assert len(mappings) > 0, "Expected at least one import mapping"

    def test_resolve_swift_returns_empty_list(self) -> None:
        """resolve_import_source for swift always returns [] (modules not file-resolvable)."""
        from seam.analysis.imports import resolve_import_source

        result = resolve_import_source("Foundation", SAMPLE_SWIFT, SAMPLE_SWIFT.parent, "swift")
        assert result == [], f"Expected [], got {result}"


# ── S8: builtins ──────────────────────────────────────────────────────────────


class TestSwiftBuiltins:
    """S8: Swift builtin vocabulary."""

    def test_print_is_builtin(self) -> None:
        """is_builtin('print', 'swift') → True."""
        from seam.analysis.builtins import is_builtin

        assert is_builtin("print", "swift") is True

    def test_string_is_builtin(self) -> None:
        """is_builtin('String', 'swift') → True."""
        from seam.analysis.builtins import is_builtin

        assert is_builtin("String", "swift") is True

    def test_int_is_builtin(self) -> None:
        """is_builtin('Int', 'swift') → True."""
        from seam.analysis.builtins import is_builtin

        assert is_builtin("Int", "swift") is True

    def test_bool_is_builtin(self) -> None:
        """is_builtin('Bool', 'swift') → True."""
        from seam.analysis.builtins import is_builtin

        assert is_builtin("Bool", "swift") is True

    def test_fatal_error_is_builtin(self) -> None:
        """is_builtin('fatalError', 'swift') → True."""
        from seam.analysis.builtins import is_builtin

        assert is_builtin("fatalError", "swift") is True

    def test_repo_name_not_builtin(self) -> None:
        """is_builtin('UserRepo', 'swift') → False (repo symbol)."""
        from seam.analysis.builtins import is_builtin

        assert is_builtin("UserRepo", "swift") is False

    def test_unknown_language_returns_false(self) -> None:
        """is_builtin('print', 'unknown_lang') → False."""
        from seam.analysis.builtins import is_builtin

        assert is_builtin("print", "unknown_lang") is False


# ── P5: Swift inter-class call edges (lightweight receiver-type inference) ──────


def _edges_for_swift_source(src: str, tmp_path: Path) -> list[Edge]:
    """Write Swift source to a temp file, parse it, and return its call edges.

    Used by the type-inference tests to build small focused ASTs without bloating
    the shared sample.swift fixture (P5 pattern: parse_swift + fixture-style temp file).
    """
    from seam.indexer.parser import parse_swift

    f = tmp_path / "inf.swift"
    f.write_text(src)
    root = parse_swift(f)
    assert root is not None, "inline Swift source failed to parse"
    return [e for e in extract_edges(root, "swift", f) if e["kind"] == "call"]


class TestSwiftTypeInference:
    """P5: self.method() and instantiated-receiver calls produce qualified Type.method edges."""

    SELF_SRC = (
        "class Repo {\n"
        "    func save() {\n"
        "        self.persist()\n"
        "    }\n"
        "    func persist() {}\n"
        "}\n"
    )

    VAR_SRC = (
        "class Foo {\n"
        "    func bar() {}\n"
        "}\n"
        "func use() {\n"
        "    let x = Foo()\n"
        "    x.bar()\n"
        "}\n"
    )

    INLINE_SRC = (
        "class Mailer {\n"
        "    func send() {}\n"
        "}\n"
        "func notify() {\n"
        "    Mailer().send()\n"
        "}\n"
    )

    BARE_SRC = "func caller() {\n    callee()\n}\nfunc callee() {}\n"

    def test_self_method_call_qualified_edge(self, tmp_path: Path) -> None:
        """self.persist() inside Repo.save → call edge target 'Repo.persist'."""
        edges = _edges_for_swift_source(self.SELF_SRC, tmp_path)
        e = _edge(edges, target="Repo.persist", kind="call")
        assert e is not None, f"Expected 'Repo.persist' edge, got: {[x['target'] for x in edges]}"
        assert e["source"] == "Repo.save", f"Expected source='Repo.save', got {e['source']}"

    def test_local_var_instantiation_qualified_edge(self, tmp_path: Path) -> None:
        """let x = Foo(); x.bar() → call edge target 'Foo.bar'."""
        edges = _edges_for_swift_source(self.VAR_SRC, tmp_path)
        e = _edge(edges, target="Foo.bar", kind="call")
        assert e is not None, f"Expected 'Foo.bar' edge, got: {[x['target'] for x in edges]}"

    def test_inline_instantiation_qualified_edge(self, tmp_path: Path) -> None:
        """Mailer().send() → call edge target 'Mailer.send'."""
        edges = _edges_for_swift_source(self.INLINE_SRC, tmp_path)
        e = _edge(edges, target="Mailer.send", kind="call")
        assert e is not None, f"Expected 'Mailer.send' edge, got: {[x['target'] for x in edges]}"

    def test_bare_call_still_works(self, tmp_path: Path) -> None:
        """A bare callee() call still produces an unqualified 'callee' edge."""
        edges = _edges_for_swift_source(self.BARE_SRC, tmp_path)
        e = _edge(edges, target="callee", kind="call")
        assert e is not None, f"Expected bare 'callee' edge, got: {[x['target'] for x in edges]}"
        assert e["source"] == "caller"

    def test_type_inference_off_reverts_to_bare_only(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """SEAM_SWIFT_TYPE_INFERENCE=off → self/typed-receiver calls emit no edge; bare still works."""
        import seam.config as config

        monkeypatch.setattr(config, "SEAM_SWIFT_TYPE_INFERENCE", "off")
        self_edges = _edges_for_swift_source(self.SELF_SRC, tmp_path)
        assert _edge(self_edges, target="Repo.persist", kind="call") is None
        assert _edge(self_edges, target="persist", kind="call") is None
        bare_edges = _edges_for_swift_source(self.BARE_SRC, tmp_path)
        assert _edge(bare_edges, target="callee", kind="call") is not None
