"""Tests for Java and C# language support (Phase 9).

TDD: Tests written before / alongside implementation. Each group covers one
behavioral slice:

J1 — Java symbols:   class→class, interface→interface, enum→type,
                     record→class, method→Class.method, constructor→Class.ctor,
                     docstring from Javadoc block_comment.
J2 — C# symbols:     class/struct→class, interface→interface, enum/delegate→type,
                     method→Class.method, constructor→Class.ctor,
                     docstring from /// comments; namespace traversed, not emitted.
J3 — Java edges:     import edge (last segment); bare-identifier call edge.
J4 — C# edges:       using_directive import edge (last segment);
                     bare-identifier invocation_expression call edge.
J5 — Java comments:  WHY/HACK/NOTE markers from // and /* */ comments.
J6 — C# comments:    WHY/HACK/NOTE markers from // and /// comments.
J7 — parser:         parse_java / parse_csharp return Node for valid source,
                     None for binary / missing files.
J8 — pipeline:       _dispatch_parser routes .java/.cs; index_one_file indexes
                     the fixtures.
J9 — signatures:     extract_node_fields returns non-None signature + visibility
                     and non-empty decorators list for annotated/attributed symbols.
J10 — imports_ext:   extract_import_mappings returns bindings for Java imports
                     and C# using directives.
J11 — builtins:      is_builtin true for known Java/C# builtins, false for repo names.

All assertions go through the PUBLIC API (extract_symbols / extract_edges /
extract_comments, extract_node_fields, extract_import_mappings, is_builtin),
never against internals.
"""

from pathlib import Path

from seam.indexer.graph import Edge, Symbol, extract_comments, extract_edges, extract_symbols

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"
SAMPLE_JAVA = FIXTURES_DIR / "sample.java"
SAMPLE_CS = FIXTURES_DIR / "sample.cs"


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


def _get_java_root():  # type: ignore[return]
    """Parse sample.java and return root node (fails test if parse fails)."""
    from seam.indexer.parser import parse_java

    node = parse_java(SAMPLE_JAVA)
    assert node is not None, "sample.java failed to parse"
    return node


def _get_cs_root():  # type: ignore[return]
    """Parse sample.cs and return root node (fails test if parse fails)."""
    from seam.indexer.parser import parse_csharp

    node = parse_csharp(SAMPLE_CS)
    assert node is not None, "sample.cs failed to parse"
    return node


# ── J1: Java symbols ───────────────────────────────────────────────────────────


class TestJavaSymbols:
    """J1: Java symbol extraction covers all required kinds and docstrings."""

    def test_class_extracted_as_class(self) -> None:
        """class DataStore → kind='class'."""
        root = _get_java_root()
        symbols = extract_symbols(root, "java", SAMPLE_JAVA)
        sym = _sym(symbols, "DataStore")
        assert sym is not None, f"DataStore not found. Symbols: {[s['name'] for s in symbols]}"
        assert sym["kind"] == "class"

    def test_interface_extracted_as_interface(self) -> None:
        """interface DataRepository → kind='interface'."""
        root = _get_java_root()
        symbols = extract_symbols(root, "java", SAMPLE_JAVA)
        sym = _sym(symbols, "DataRepository")
        assert sym is not None, f"DataRepository not found. Symbols: {[s['name'] for s in symbols]}"
        assert sym["kind"] == "interface"

    def test_enum_extracted_as_type(self) -> None:
        """enum EntityStatus → kind='type'."""
        root = _get_java_root()
        symbols = extract_symbols(root, "java", SAMPLE_JAVA)
        sym = _sym(symbols, "EntityStatus")
        assert sym is not None, f"EntityStatus not found. Symbols: {[s['name'] for s in symbols]}"
        assert sym["kind"] == "type"

    def test_record_extracted_as_class(self) -> None:
        """record GeoPoint → kind='class'."""
        root = _get_java_root()
        symbols = extract_symbols(root, "java", SAMPLE_JAVA)
        sym = _sym(symbols, "GeoPoint")
        assert sym is not None, f"GeoPoint not found. Symbols: {[s['name'] for s in symbols]}"
        assert sym["kind"] == "class"

    def test_method_qualified_as_class_method(self) -> None:
        """public void save(String) inside DataStore → name='DataStore.save', kind='method'."""
        root = _get_java_root()
        symbols = extract_symbols(root, "java", SAMPLE_JAVA)
        sym = _sym(symbols, "DataStore.save")
        assert sym is not None, (
            "DataStore.save not found — method not qualified. "
            f"Symbols: {[s['name'] for s in symbols]}"
        )
        assert sym["kind"] == "method"

    def test_constructor_qualified_as_class_ctor(self) -> None:
        """constructor DataStore(Map) inside DataStore → name='DataStore.DataStore', kind='method'."""
        root = _get_java_root()
        symbols = extract_symbols(root, "java", SAMPLE_JAVA)
        sym = _sym(symbols, "DataStore.DataStore")
        assert sym is not None, (
            f"DataStore.DataStore (constructor) not found. Symbols: {[s['name'] for s in symbols]}"
        )
        assert sym["kind"] == "method"

    def test_class_with_javadoc_has_docstring(self) -> None:
        """DataStore preceded by /** block_comment → docstring captured."""
        root = _get_java_root()
        symbols = extract_symbols(root, "java", SAMPLE_JAVA)
        sym = _sym(symbols, "DataStore")
        assert sym is not None
        assert sym["docstring"] is not None, "DataStore should have a Javadoc docstring"
        assert "primary repository" in sym["docstring"] or "DataStore" in sym["docstring"]

    def test_interface_has_docstring(self) -> None:
        """DataRepository preceded by /** → docstring captured."""
        root = _get_java_root()
        symbols = extract_symbols(root, "java", SAMPLE_JAVA)
        sym = _sym(symbols, "DataRepository")
        assert sym is not None
        assert sym["docstring"] is not None, "DataRepository should have a Javadoc docstring"

    def test_method_has_docstring(self) -> None:
        """DataStore.save preceded by /** → docstring captured."""
        root = _get_java_root()
        symbols = extract_symbols(root, "java", SAMPLE_JAVA)
        sym = _sym(symbols, "DataStore.save")
        assert sym is not None
        assert sym["docstring"] is not None, "DataStore.save should have a Javadoc docstring"

    def test_start_line_is_one_based(self) -> None:
        """Symbol start_line is 1-based and ≥ 1."""
        root = _get_java_root()
        symbols = extract_symbols(root, "java", SAMPLE_JAVA)
        assert len(symbols) > 0
        for sym in symbols:
            assert sym["start_line"] >= 1, f"start_line < 1 for {sym['name']}"

    def test_file_path_set_correctly(self) -> None:
        """Symbol file field matches the fixture path string."""
        root = _get_java_root()
        symbols = extract_symbols(root, "java", SAMPLE_JAVA)
        for sym in symbols:
            assert sym["file"] == str(SAMPLE_JAVA), f"file mismatch for {sym['name']}"

    def test_returns_list_not_none(self) -> None:
        """extract_symbols always returns a list, never None."""
        root = _get_java_root()
        result = extract_symbols(root, "java", SAMPLE_JAVA)
        assert isinstance(result, list)


# ── J2: C# symbols ─────────────────────────────────────────────────────────────


class TestCSharpSymbols:
    """J2: C# symbol extraction covers all required kinds and docstrings."""

    def test_class_extracted_as_class(self) -> None:
        """class DataStore → kind='class'."""
        root = _get_cs_root()
        symbols = extract_symbols(root, "csharp", SAMPLE_CS)
        sym = _sym(symbols, "DataStore")
        assert sym is not None, f"DataStore not found. Symbols: {[s['name'] for s in symbols]}"
        assert sym["kind"] == "class"

    def test_interface_extracted_as_interface(self) -> None:
        """interface IDataRepository → kind='interface'."""
        root = _get_cs_root()
        symbols = extract_symbols(root, "csharp", SAMPLE_CS)
        sym = _sym(symbols, "IDataRepository")
        assert sym is not None, (
            f"IDataRepository not found. Symbols: {[s['name'] for s in symbols]}"
        )
        assert sym["kind"] == "interface"

    def test_enum_extracted_as_type(self) -> None:
        """enum EntityStatus → kind='type'."""
        root = _get_cs_root()
        symbols = extract_symbols(root, "csharp", SAMPLE_CS)
        sym = _sym(symbols, "EntityStatus")
        assert sym is not None, f"EntityStatus not found. Symbols: {[s['name'] for s in symbols]}"
        assert sym["kind"] == "type"

    def test_delegate_extracted_as_type(self) -> None:
        """delegate NotifyCallback → kind='type'."""
        root = _get_cs_root()
        symbols = extract_symbols(root, "csharp", SAMPLE_CS)
        sym = _sym(symbols, "NotifyCallback")
        assert sym is not None, f"NotifyCallback not found. Symbols: {[s['name'] for s in symbols]}"
        assert sym["kind"] == "type"

    def test_struct_extracted_as_class(self) -> None:
        """struct DataView → kind='class'."""
        root = _get_cs_root()
        symbols = extract_symbols(root, "csharp", SAMPLE_CS)
        sym = _sym(symbols, "DataView")
        assert sym is not None, f"DataView not found. Symbols: {[s['name'] for s in symbols]}"
        assert sym["kind"] == "class"

    def test_method_qualified_as_class_method(self) -> None:
        """public void Save(string) inside DataStore → name='DataStore.Save', kind='method'."""
        root = _get_cs_root()
        symbols = extract_symbols(root, "csharp", SAMPLE_CS)
        sym = _sym(symbols, "DataStore.Save")
        assert sym is not None, (
            "DataStore.Save not found — method not qualified. "
            f"Symbols: {[s['name'] for s in symbols]}"
        )
        assert sym["kind"] == "method"

    def test_constructor_qualified_as_class_ctor(self) -> None:
        """constructor DataStore(dict) → name='DataStore.DataStore', kind='method'."""
        root = _get_cs_root()
        symbols = extract_symbols(root, "csharp", SAMPLE_CS)
        sym = _sym(symbols, "DataStore.DataStore")
        assert sym is not None, (
            f"DataStore.DataStore (constructor) not found. Symbols: {[s['name'] for s in symbols]}"
        )
        assert sym["kind"] == "method"

    def test_namespace_not_emitted_as_symbol(self) -> None:
        """namespace SampleApp.Services is traversed, NOT emitted as a symbol."""
        root = _get_cs_root()
        symbols = extract_symbols(root, "csharp", SAMPLE_CS)
        names = {s["name"] for s in symbols}
        assert "SampleApp" not in names
        assert "SampleApp.Services" not in names

    def test_class_has_docstring(self) -> None:
        """DataStore preceded by /// comments → docstring captured."""
        root = _get_cs_root()
        symbols = extract_symbols(root, "csharp", SAMPLE_CS)
        sym = _sym(symbols, "DataStore")
        assert sym is not None
        assert sym["docstring"] is not None, "DataStore should have a /// docstring"

    def test_returns_list_not_none(self) -> None:
        """extract_symbols always returns a list, never None."""
        root = _get_cs_root()
        result = extract_symbols(root, "csharp", SAMPLE_CS)
        assert isinstance(result, list)

    def test_start_line_is_one_based(self) -> None:
        """Symbol start_line is 1-based and ≥ 1."""
        root = _get_cs_root()
        symbols = extract_symbols(root, "csharp", SAMPLE_CS)
        for sym in symbols:
            assert sym["start_line"] >= 1, f"start_line < 1 for {sym['name']}"


# ── J3: Java edges ─────────────────────────────────────────────────────────────


class TestJavaEdges:
    """J3: Java import and call edges are extracted correctly."""

    def test_import_edge_produced(self) -> None:
        """Java import statement produces at least one import edge."""
        root = _get_java_root()
        edges = extract_edges(root, "java", SAMPLE_JAVA)
        import_edges = [e for e in edges if e["kind"] == "import"]
        assert len(import_edges) > 0, "No import edges found for Java fixture"

    def test_import_target_is_last_segment(self) -> None:
        """import java.util.List → edge target is 'List' (last segment)."""
        root = _get_java_root()
        edges = extract_edges(root, "java", SAMPLE_JAVA)
        targets = {e["target"] for e in edges if e["kind"] == "import"}
        assert "List" in targets, f"Expected 'List' import edge, got targets: {targets}"

    def test_import_source_is_file_stem(self) -> None:
        """Import edge source is the file stem ('sample')."""
        root = _get_java_root()
        edges = extract_edges(root, "java", SAMPLE_JAVA)
        for e in edges:
            if e["kind"] == "import" and e["target"] == "List":
                assert e["source"] == "sample", f"Expected source='sample', got {e['source']}"

    def test_call_edge_produced(self) -> None:
        """Bare-identifier method_invocation produces a call edge."""
        root = _get_java_root()
        edges = extract_edges(root, "java", SAMPLE_JAVA)
        call_edges = [e for e in edges if e["kind"] == "call"]
        assert len(call_edges) > 0, "No call edges found for Java fixture"

    def test_call_edge_target_is_bare_callee(self) -> None:
        """DataStore.save calls persist() → call edge target is 'persist'."""
        root = _get_java_root()
        edges = extract_edges(root, "java", SAMPLE_JAVA)
        e = _edge(edges, target="persist", kind="call")
        assert e is not None, (
            f"Expected call edge targeting 'persist'. "
            f"Call targets: {[e['target'] for e in edges if e['kind'] == 'call']}"
        )

    def test_member_call_not_extracted(self) -> None:
        """this.store.put() is a member call — NOT extracted as bare-identifier call."""
        root = _get_java_root()
        edges = extract_edges(root, "java", SAMPLE_JAVA)
        call_targets = {e["target"] for e in edges if e["kind"] == "call"}
        # "put" should not appear — it's called on this.store (has object field)
        assert "put" not in call_targets, (
            "'put' is a member call on this.store and should not be extracted"
        )

    def test_call_edge_source_is_enclosing_method(self) -> None:
        """The call edge source is the enclosing method qualified name."""
        root = _get_java_root()
        edges = extract_edges(root, "java", SAMPLE_JAVA)
        e = _edge(edges, target="persist", kind="call")
        assert e is not None
        # 'persist' is called inside DataStore.save
        assert e["source"] == "DataStore.save", (
            f"Expected source='DataStore.save', got {e['source']}"
        )

    def test_edges_have_confidence_field(self) -> None:
        """All edges carry a valid confidence field."""
        root = _get_java_root()
        edges = extract_edges(root, "java", SAMPLE_JAVA)
        for e in edges:
            assert e["confidence"] in ("EXTRACTED", "INFERRED", "AMBIGUOUS"), (
                f"Edge missing valid confidence: {e}"
            )


# ── J4: C# edges ───────────────────────────────────────────────────────────────


class TestCSharpEdges:
    """J4: C# import and call edges are extracted correctly."""

    def test_import_edge_produced(self) -> None:
        """using directive produces at least one import edge."""
        root = _get_cs_root()
        edges = extract_edges(root, "csharp", SAMPLE_CS)
        import_edges = [e for e in edges if e["kind"] == "import"]
        assert len(import_edges) > 0, "No import edges found for C# fixture"

    def test_import_target_is_last_segment(self) -> None:
        """using System.Collections.Generic → edge target is 'Generic' (last segment)."""
        root = _get_cs_root()
        edges = extract_edges(root, "csharp", SAMPLE_CS)
        targets = {e["target"] for e in edges if e["kind"] == "import"}
        assert "Generic" in targets, f"Expected 'Generic' import edge, got targets: {targets}"

    def test_import_source_is_file_stem(self) -> None:
        """Import edge source is the file stem ('sample')."""
        root = _get_cs_root()
        edges = extract_edges(root, "csharp", SAMPLE_CS)
        for e in edges:
            if e["kind"] == "import" and e["target"] == "Generic":
                assert e["source"] == "sample", f"Expected source='sample', got {e['source']}"

    def test_call_edge_produced(self) -> None:
        """Bare-identifier invocation_expression produces a call edge."""
        root = _get_cs_root()
        edges = extract_edges(root, "csharp", SAMPLE_CS)
        call_edges = [e for e in edges if e["kind"] == "call"]
        assert len(call_edges) > 0, "No call edges found for C# fixture"

    def test_call_edge_target_is_bare_callee(self) -> None:
        """DataStore.Save calls Persist() → call edge target is 'Persist'."""
        root = _get_cs_root()
        edges = extract_edges(root, "csharp", SAMPLE_CS)
        e = _edge(edges, target="Persist", kind="call")
        assert e is not None, (
            f"Expected call edge targeting 'Persist'. "
            f"Call targets: {[e['target'] for e in edges if e['kind'] == 'call']}"
        )

    def test_member_call_not_extracted(self) -> None:
        """this.Helper() / _store[key] are member accesses — NOT extracted as bare calls."""
        root = _get_cs_root()
        edges = extract_edges(root, "csharp", SAMPLE_CS)
        call_targets = {e["target"] for e in edges if e["kind"] == "call"}
        # member_access_expression should be skipped
        assert "Clear" not in call_targets, (
            "'Clear' is called on _store (member access) and should not be extracted"
        )

    def test_call_edge_source_is_enclosing_method(self) -> None:
        """The call source is the enclosing method's qualified name."""
        root = _get_cs_root()
        edges = extract_edges(root, "csharp", SAMPLE_CS)
        e = _edge(edges, target="Persist", kind="call")
        assert e is not None
        assert e["source"] == "DataStore.Save", (
            f"Expected source='DataStore.Save', got {e['source']}"
        )

    def test_edges_have_confidence_field(self) -> None:
        """All edges carry a valid confidence field."""
        root = _get_cs_root()
        edges = extract_edges(root, "csharp", SAMPLE_CS)
        for e in edges:
            assert e["confidence"] in ("EXTRACTED", "INFERRED", "AMBIGUOUS")


# ── J5: Java comments ──────────────────────────────────────────────────────────


class TestJavaComments:
    """J5: Java semantic comment extraction from // and /* */ comments."""

    def test_why_marker_extracted(self) -> None:
        """// WHY: ... or /* WHY: ... */ → Comment with marker='WHY'."""
        root = _get_java_root()
        comments = extract_comments(root, "java", SAMPLE_JAVA)
        whys = [c for c in comments if c["marker"] == "WHY"]
        assert len(whys) > 0, f"No WHY marker found. All comments: {comments}"

    def test_hack_marker_extracted(self) -> None:
        """// HACK: ... → Comment with marker='HACK'."""
        root = _get_java_root()
        comments = extract_comments(root, "java", SAMPLE_JAVA)
        hacks = [c for c in comments if c["marker"] == "HACK"]
        assert len(hacks) > 0, f"No HACK marker found. All comments: {comments}"

    def test_note_marker_extracted(self) -> None:
        """// NOTE: ... → Comment with marker='NOTE'."""
        root = _get_java_root()
        comments = extract_comments(root, "java", SAMPLE_JAVA)
        notes = [c for c in comments if c["marker"] == "NOTE"]
        assert len(notes) > 0, f"No NOTE marker found. All comments: {comments}"

    def test_plain_comment_not_extracted(self) -> None:
        """Plain // comment without marker → not extracted."""
        root = _get_java_root()
        comments = extract_comments(root, "java", SAMPLE_JAVA)
        texts = {c["text"] for c in comments}
        # The "Private helpers below" comment has no marker keyword
        assert not any("Private helpers" in t for t in texts)

    def test_comment_has_correct_fields(self) -> None:
        """Each extracted comment has marker, text, and line fields."""
        root = _get_java_root()
        comments = extract_comments(root, "java", SAMPLE_JAVA)
        valid_markers = {"WHY", "HACK", "NOTE", "TODO", "FIXME"}
        for c in comments:
            assert "marker" in c
            assert "text" in c
            assert "line" in c
            assert c["marker"] in valid_markers
            assert isinstance(c["line"], int)
            assert c["line"] >= 1

    def test_returns_list_not_none(self) -> None:
        """extract_comments always returns a list, never raises."""
        root = _get_java_root()
        result = extract_comments(root, "java", SAMPLE_JAVA)
        assert isinstance(result, list)


# ── J6: C# comments ────────────────────────────────────────────────────────────


class TestCSharpComments:
    """J6: C# semantic comment extraction from // and /// comments."""

    def test_why_marker_extracted(self) -> None:
        """// WHY: ... or /// WHY: ... → Comment with marker='WHY'."""
        root = _get_cs_root()
        comments = extract_comments(root, "csharp", SAMPLE_CS)
        whys = [c for c in comments if c["marker"] == "WHY"]
        assert len(whys) > 0, f"No WHY marker found. All comments: {comments}"

    def test_hack_marker_extracted(self) -> None:
        """// HACK: ... → Comment with marker='HACK'."""
        root = _get_cs_root()
        comments = extract_comments(root, "csharp", SAMPLE_CS)
        hacks = [c for c in comments if c["marker"] == "HACK"]
        assert len(hacks) > 0, f"No HACK marker found. All comments: {comments}"

    def test_note_marker_extracted(self) -> None:
        """// NOTE: ... or /// NOTE: ... → Comment with marker='NOTE'."""
        root = _get_cs_root()
        comments = extract_comments(root, "csharp", SAMPLE_CS)
        notes = [c for c in comments if c["marker"] == "NOTE"]
        assert len(notes) > 0, f"No NOTE marker found. All comments: {comments}"

    def test_plain_comment_not_extracted(self) -> None:
        """Plain /// <summary> lines without marker → not extracted."""
        root = _get_cs_root()
        comments = extract_comments(root, "csharp", SAMPLE_CS)
        texts = {c["text"] for c in comments}
        assert not any("<summary>" in t for t in texts)

    def test_comment_fields_valid(self) -> None:
        """All extracted comments have valid marker, text, and line fields."""
        root = _get_cs_root()
        comments = extract_comments(root, "csharp", SAMPLE_CS)
        valid_markers = {"WHY", "HACK", "NOTE", "TODO", "FIXME"}
        for c in comments:
            assert c["marker"] in valid_markers
            assert isinstance(c["line"], int)
            assert c["line"] >= 1

    def test_returns_list_not_none(self) -> None:
        """extract_comments always returns a list, never raises."""
        root = _get_cs_root()
        result = extract_comments(root, "csharp", SAMPLE_CS)
        assert isinstance(result, list)


# ── J7: parser ────────────────────────────────────────────────────────────────


class TestJavaCSharpParser:
    """J7: parse_java and parse_csharp behave like the existing parsers."""

    def test_parse_java_valid_file_returns_node(self) -> None:
        """parse_java(sample.java) returns a non-None AST root node."""
        from seam.indexer.parser import parse_java

        node = parse_java(SAMPLE_JAVA)
        assert node is not None

    def test_parse_java_node_has_children(self) -> None:
        """Root node from sample.java has at least one child."""
        from seam.indexer.parser import parse_java

        node = parse_java(SAMPLE_JAVA)
        assert node is not None
        assert len(node.children) > 0  # type: ignore[union-attr]

    def test_parse_java_binary_file_returns_none(self, tmp_path: Path) -> None:
        """Binary .java file → None (not raised)."""
        from seam.indexer.parser import parse_java

        binary_file = tmp_path / "binary.java"
        binary_file.write_bytes(b"public class Main {}\x00rest")
        node = parse_java(binary_file)
        assert node is None

    def test_parse_java_missing_file_returns_none(self) -> None:
        """Non-existent .java path → None."""
        from seam.indexer.parser import parse_java

        node = parse_java(Path("/nonexistent/path/file.java"))
        assert node is None

    def test_parse_csharp_valid_file_returns_node(self) -> None:
        """parse_csharp(sample.cs) returns a non-None AST root node."""
        from seam.indexer.parser import parse_csharp

        node = parse_csharp(SAMPLE_CS)
        assert node is not None

    def test_parse_csharp_node_has_children(self) -> None:
        """Root node from sample.cs has at least one child."""
        from seam.indexer.parser import parse_csharp

        node = parse_csharp(SAMPLE_CS)
        assert node is not None
        assert len(node.children) > 0  # type: ignore[union-attr]

    def test_parse_csharp_binary_file_returns_none(self, tmp_path: Path) -> None:
        """Binary .cs file → None."""
        from seam.indexer.parser import parse_csharp

        binary_file = tmp_path / "binary.cs"
        binary_file.write_bytes(b"public class Foo {}\x00")
        node = parse_csharp(binary_file)
        assert node is None

    def test_parse_csharp_missing_file_returns_none(self) -> None:
        """Non-existent .cs path → None."""
        from seam.indexer.parser import parse_csharp

        node = parse_csharp(Path("/nonexistent/path/file.cs"))
        assert node is None


# ── J8: pipeline ───────────────────────────────────────────────────────────────


class TestJavaCSharpPipeline:
    """J8: _dispatch_parser routes .java/.cs; index_one_file indexes the fixtures."""

    def test_dispatch_parser_java(self, tmp_path: Path) -> None:
        """_dispatch_parser routes language='java' to parse_java."""
        from seam.indexer.pipeline import _dispatch_parser

        java_file = tmp_path / "test.java"
        java_file.write_text("public class Test {}\n")
        result = _dispatch_parser(java_file, "java")
        assert result is not None, "_dispatch_parser returned None for Java source"

    def test_dispatch_parser_csharp(self, tmp_path: Path) -> None:
        """_dispatch_parser routes language='csharp' to parse_csharp."""
        from seam.indexer.pipeline import _dispatch_parser

        cs_file = tmp_path / "test.cs"
        cs_file.write_text("public class Test {}\n")
        result = _dispatch_parser(cs_file, "csharp")
        assert result is not None, "_dispatch_parser returned None for C# source"

    def test_language_map_has_java(self) -> None:
        """config.SEAM_LANGUAGE_MAP includes '.java' → 'java'."""
        import seam.config as config

        assert ".java" in config.SEAM_LANGUAGE_MAP
        assert config.SEAM_LANGUAGE_MAP[".java"] == "java"

    def test_language_map_has_csharp(self) -> None:
        """config.SEAM_LANGUAGE_MAP includes '.cs' → 'csharp'."""
        import seam.config as config

        assert ".cs" in config.SEAM_LANGUAGE_MAP
        assert config.SEAM_LANGUAGE_MAP[".cs"] == "csharp"

    def test_index_one_file_java_fixture(self, tmp_path: Path) -> None:
        """index_one_file on sample.java returns (symbols>0, edges≥0)."""
        from seam.indexer.db import init_db
        from seam.indexer.pipeline import index_one_file

        db_path = tmp_path / "seam.db"
        conn = init_db(db_path)
        result = index_one_file(conn, SAMPLE_JAVA)
        conn.close()
        assert result is not None, "index_one_file returned None for sample.java"
        sym_count, _edge_count = result
        assert sym_count > 0, f"Expected symbols > 0 for sample.java, got {sym_count}"

    def test_index_one_file_csharp_fixture(self, tmp_path: Path) -> None:
        """index_one_file on sample.cs returns (symbols>0, edges≥0)."""
        from seam.indexer.db import init_db
        from seam.indexer.pipeline import index_one_file

        db_path = tmp_path / "seam.db"
        conn = init_db(db_path)
        result = index_one_file(conn, SAMPLE_CS)
        conn.close()
        assert result is not None, "index_one_file returned None for sample.cs"
        sym_count, _edge_count = result
        assert sym_count > 0, f"Expected symbols > 0 for sample.cs, got {sym_count}"

    def test_walk_project_finds_java_files(self, tmp_path: Path) -> None:
        """walk_project includes .java files after language map update."""
        from seam.indexer.pipeline import walk_project

        java_file = tmp_path / "Main.java"
        java_file.write_text("public class Main {}\n")
        files = walk_project(tmp_path)
        paths = {f.name for f in files}
        assert "Main.java" in paths, f"Main.java not found in walk_project: {paths}"

    def test_walk_project_finds_csharp_files(self, tmp_path: Path) -> None:
        """walk_project includes .cs files after language map update."""
        from seam.indexer.pipeline import walk_project

        cs_file = tmp_path / "App.cs"
        cs_file.write_text("public class App {}\n")
        files = walk_project(tmp_path)
        paths = {f.name for f in files}
        assert "App.cs" in paths, f"App.cs not found in walk_project: {paths}"


# ── J9: signatures ─────────────────────────────────────────────────────────────


class TestJavaCSharpSignatures:
    """J9: extract_node_fields returns enrichment for Java and C# nodes."""

    def test_java_class_has_signature(self) -> None:
        """Java class_declaration → signature is not None."""
        root = _get_java_root()
        symbols = extract_symbols(root, "java", SAMPLE_JAVA)
        sym = _sym(symbols, "DataStore")
        assert sym is not None
        assert sym["signature"] is not None, "DataStore should have a signature"
        assert "DataStore" in sym["signature"]

    def test_java_method_has_signature(self) -> None:
        """Java method_declaration → signature is not None."""
        root = _get_java_root()
        symbols = extract_symbols(root, "java", SAMPLE_JAVA)
        sym = _sym(symbols, "DataStore.save")
        assert sym is not None
        assert sym["signature"] is not None, "DataStore.save should have a signature"

    def test_java_public_class_is_exported(self) -> None:
        """Public Java class → is_exported=True."""
        root = _get_java_root()
        symbols = extract_symbols(root, "java", SAMPLE_JAVA)
        sym = _sym(symbols, "DataStore")
        assert sym is not None
        assert sym["is_exported"] is True, "public class DataStore should be exported"

    def test_java_class_visibility_is_public(self) -> None:
        """Public Java class → visibility='public'."""
        root = _get_java_root()
        symbols = extract_symbols(root, "java", SAMPLE_JAVA)
        sym = _sym(symbols, "DataStore")
        assert sym is not None
        assert sym["visibility"] == "public"

    def test_java_private_method_visibility(self) -> None:
        """Private Java method → visibility='private', is_exported=False."""
        root = _get_java_root()
        symbols = extract_symbols(root, "java", SAMPLE_JAVA)
        sym = _sym(symbols, "DataStore.init")
        assert sym is not None
        assert sym["visibility"] == "private"
        assert sym["is_exported"] is False

    def test_java_annotated_class_has_decorators(self) -> None:
        """@Service @SuppressWarnings class → decorators list is non-empty."""
        root = _get_java_root()
        symbols = extract_symbols(root, "java", SAMPLE_JAVA)
        sym = _sym(symbols, "DataStore")
        assert sym is not None
        assert len(sym["decorators"]) > 0, (
            "DataStore has @Service and @SuppressWarnings — decorators should be non-empty"
        )

    def test_csharp_class_has_signature(self) -> None:
        """C# class_declaration → signature is not None."""
        root = _get_cs_root()
        symbols = extract_symbols(root, "csharp", SAMPLE_CS)
        sym = _sym(symbols, "DataStore")
        assert sym is not None
        assert sym["signature"] is not None, "DataStore should have a signature"
        assert "DataStore" in sym["signature"]

    def test_csharp_public_class_is_exported(self) -> None:
        """Public C# class → is_exported=True."""
        root = _get_cs_root()
        symbols = extract_symbols(root, "csharp", SAMPLE_CS)
        sym = _sym(symbols, "DataStore")
        assert sym is not None
        assert sym["is_exported"] is True

    def test_csharp_class_visibility_is_public(self) -> None:
        """Public C# class → visibility='public'."""
        root = _get_cs_root()
        symbols = extract_symbols(root, "csharp", SAMPLE_CS)
        sym = _sym(symbols, "DataStore")
        assert sym is not None
        assert sym["visibility"] == "public"

    def test_csharp_attributed_class_has_decorators(self) -> None:
        """[Serializable] [DataContract] class → decorators list is non-empty."""
        root = _get_cs_root()
        symbols = extract_symbols(root, "csharp", SAMPLE_CS)
        sym = _sym(symbols, "DataStore")
        assert sym is not None
        assert len(sym["decorators"]) > 0, (
            "DataStore has [Serializable] and [DataContract] — decorators should be non-empty"
        )


# ── J10: imports_ext ────────────────────────────────────────────────────────────


class TestJavaCSharpImports:
    """J10: extract_import_mappings returns bindings for Java and C#."""

    def test_java_import_binding_extracted(self) -> None:
        """Java 'import java.util.List' → at least one ImportMapping with local_name='List'."""
        from seam.analysis.imports import extract_import_mappings
        from seam.indexer.parser import parse_java

        root = parse_java(SAMPLE_JAVA)
        assert root is not None
        mappings = extract_import_mappings(root, SAMPLE_JAVA, "java")
        local_names = [m["local_name"] for m in mappings]
        assert "List" in local_names, f"Expected 'List' in import mappings, got: {local_names}"

    def test_csharp_import_binding_extracted(self) -> None:
        """C# 'using System.Collections.Generic' → ImportMapping with local_name='Generic'."""
        from seam.analysis.imports import extract_import_mappings
        from seam.indexer.parser import parse_csharp

        root = parse_csharp(SAMPLE_CS)
        assert root is not None
        mappings = extract_import_mappings(root, SAMPLE_CS, "csharp")
        local_names = [m["local_name"] for m in mappings]
        assert "Generic" in local_names, (
            f"Expected 'Generic' in import mappings, got: {local_names}"
        )

    def test_java_import_mappings_never_raise(self, tmp_path: Path) -> None:
        """extract_import_mappings on empty Java file returns [] without raising."""
        from seam.analysis.imports import extract_import_mappings
        from seam.indexer.parser import parse_java

        empty = tmp_path / "Empty.java"
        empty.write_text("public class Empty {}\n")
        root = parse_java(empty)
        assert root is not None
        result = extract_import_mappings(root, empty, "java")
        assert isinstance(result, list)

    def test_csharp_import_mappings_never_raise(self, tmp_path: Path) -> None:
        """extract_import_mappings on empty C# file returns [] without raising."""
        from seam.analysis.imports import extract_import_mappings
        from seam.indexer.parser import parse_csharp

        empty = tmp_path / "Empty.cs"
        empty.write_text("public class Empty {}\n")
        root = parse_csharp(empty)
        assert root is not None
        result = extract_import_mappings(root, empty, "csharp")
        assert isinstance(result, list)


# ── J11: builtins ──────────────────────────────────────────────────────────────


class TestJavaCSharpBuiltins:
    """J11: is_builtin returns True for known Java/C# builtins, False for repo names."""

    def test_java_builtin_string_is_builtin(self) -> None:
        """Java 'String' is a well-known type → is_builtin True."""
        from seam.analysis.builtins import is_builtin

        assert is_builtin("String", "java") is True

    def test_java_builtin_system_is_builtin(self) -> None:
        """Java 'System' is a well-known class → is_builtin True."""
        from seam.analysis.builtins import is_builtin

        assert is_builtin("System", "java") is True

    def test_java_repo_name_is_not_builtin(self) -> None:
        """'DataStore' is a repo-specific name → is_builtin False."""
        from seam.analysis.builtins import is_builtin

        assert is_builtin("DataStore", "java") is False

    def test_csharp_builtin_console_is_builtin(self) -> None:
        """C# 'Console' is a well-known class → is_builtin True."""
        from seam.analysis.builtins import is_builtin

        assert is_builtin("Console", "csharp") is True

    def test_csharp_builtin_string_is_builtin(self) -> None:
        """C# 'string' (lowercase) is a keyword type → is_builtin True."""
        from seam.analysis.builtins import is_builtin

        assert is_builtin("string", "csharp") is True

    def test_csharp_repo_name_is_not_builtin(self) -> None:
        """'DataStore' is a repo-specific name → is_builtin False for csharp."""
        from seam.analysis.builtins import is_builtin

        assert is_builtin("DataStore", "csharp") is False

    def test_java_builtin_does_not_affect_csharp(self) -> None:
        """Builtin sets are language-scoped — Java builtins don't bleed into csharp."""
        from seam.analysis.builtins import is_builtin

        # 'Integer' is Java-specific; not a C# builtin
        assert is_builtin("Integer", "java") is True
        assert is_builtin("Integer", "csharp") is False


# ── Regression: Bug fixes (FIX SET 1) ─────────────────────────────────────────


class TestJavaWildcardImport:
    """Regression: Java wildcard import (import java.util.*;) must NOT emit a
    spurious edge/mapping with target='util'.

    Bug: the pre-fix code iterated named_children and processed scoped_identifier
    ('java.util') before checking the asterisk child, emitting 'util' as a target.
    Fix: pre-scan for asterisk first; skip entire declaration if found.
    """

    def test_wildcard_import_no_util_edge(self) -> None:
        """import java.util.*; must NOT produce an import edge with target='util'."""
        root = _get_java_root()
        edges = extract_edges(root, "java", SAMPLE_JAVA)
        import_targets = {e["target"] for e in edges if e["kind"] == "import"}
        assert "util" not in import_targets, (
            f"Wildcard import 'java.util.*' spuriously emitted 'util' edge. "
            f"Import targets: {import_targets}"
        )

    def test_wildcard_import_no_util_mapping(self) -> None:
        """import java.util.*; must NOT produce an ImportMapping with local_name='util'."""
        from seam.analysis.imports import extract_import_mappings
        from seam.indexer.parser import parse_java

        root = parse_java(SAMPLE_JAVA)
        assert root is not None
        mappings = extract_import_mappings(root, SAMPLE_JAVA, "java")
        local_names = [m["local_name"] for m in mappings]
        assert "util" not in local_names, (
            f"Wildcard import 'java.util.*' spuriously emitted 'util' mapping. "
            f"local_names: {local_names}"
        )

    def test_non_wildcard_imports_still_extracted(self) -> None:
        """Non-wildcard imports (java.util.List, java.util.Map) are unaffected by fix."""
        root = _get_java_root()
        edges = extract_edges(root, "java", SAMPLE_JAVA)
        import_targets = {e["target"] for e in edges if e["kind"] == "import"}
        assert "List" in import_targets, "import java.util.List should still produce List edge"
        assert "Map" in import_targets, "import java.util.Map should still produce Map edge"


class TestCSharpUsingAlias:
    """Regression: C# using-alias (using Coll = System.Collections.Generic;) must
    emit the namespace last segment ('Generic') as target, NOT the alias ('Coll').

    Bug: the pre-fix code iterated named_children and emitted the first identifier
    node ('Coll') as the target without checking for the alias form.
    Fix: detect presence of both identifier + qualified_name siblings → alias form.
    Use the qualified_name's last segment as the target; skip the alias identifier.
    """

    def test_using_alias_no_alias_edge(self) -> None:
        """using Coll = System.Collections.Generic; must NOT emit an edge target='Coll'."""
        root = _get_cs_root()
        edges = extract_edges(root, "csharp", SAMPLE_CS)
        import_targets = {e["target"] for e in edges if e["kind"] == "import"}
        assert "Coll" not in import_targets, (
            f"Using-alias 'using Coll = ...' spuriously emitted 'Coll' edge. "
            f"Import targets: {import_targets}"
        )

    def test_using_alias_no_alias_mapping(self) -> None:
        """using Coll = System.Collections.Generic; must NOT produce mapping local_name='Coll'
        as an imported-namespace target (alias-keyed mappings are intentional but
        the alias name 'Coll' must not appear as an import *target* edge)."""
        from seam.analysis.imports import extract_import_mappings
        from seam.indexer.parser import parse_csharp

        root = parse_csharp(SAMPLE_CS)
        assert root is not None
        mappings = extract_import_mappings(root, SAMPLE_CS, "csharp")
        # The mapping for the alias form should use 'Coll' as local_name (alias),
        # but 'Generic' as exported_name / or be absent. It must NOT use 'Coll' as exported_name.
        for m in mappings:
            assert m["local_name"] != "Coll" or m.get("exported_name") != "Coll", (
                "Using-alias should not emit Coll→Coll mapping; alias must point to real namespace"
            )

    def test_normal_using_still_extracted(self) -> None:
        """Normal using directives (System, Generic) are unaffected by alias fix."""
        root = _get_cs_root()
        edges = extract_edges(root, "csharp", SAMPLE_CS)
        import_targets = {e["target"] for e in edges if e["kind"] == "import"}
        assert "System" in import_targets, "using System; should still produce System edge"
        assert "Generic" in import_targets, (
            "using System.Collections.Generic; should still produce Generic edge"
        )


class TestJavaEnumMethods:
    """Regression: Java enum with methods must emit the methods as qualified symbols.

    Bug: the enum_declaration branch emitted the enum as 'type' but never recursed
    into enum_body_declarations, dropping methods like 'EntityStatus.label'.
    Fix: recurse into enum body declarations and emit methods qualified as 'Enum.method'.
    """

    def test_enum_method_extracted(self) -> None:
        """EntityStatus.label() inside enum body must appear as a symbol."""
        root = _get_java_root()
        symbols = extract_symbols(root, "java", SAMPLE_JAVA)
        sym = _sym(symbols, "EntityStatus.label")
        assert sym is not None, (
            "EntityStatus.label() not found — enum methods not recursed. "
            f"Symbols: {[s['name'] for s in symbols]}"
        )
        assert sym["kind"] == "method"

    def test_enum_type_symbol_still_present(self) -> None:
        """Enum type symbol (EntityStatus) is still emitted alongside its methods."""
        root = _get_java_root()
        symbols = extract_symbols(root, "java", SAMPLE_JAVA)
        sym = _sym(symbols, "EntityStatus")
        assert sym is not None, "EntityStatus (enum type) must still be present"
        assert sym["kind"] == "type"
