"""Tests for Tier B slice B6 — instantiates edges across all 12 languages.

TDD: tests written before implementation.

Acceptance criteria from issue #65:
- Per-language construction detection emits kind='instantiates' edges to the
  constructed type name (all 12 langs).
- 'instantiates' added to the edge kind vocabulary; no schema change.
- instantiates edges visible via extract_edges (and thus context/impact).
- call edges unaffected.
- Never raises on unusual construction shapes.
- Per-language tests for instantiates detection; make gate green.

Construction patterns per language:
  Python:      Foo()  — PascalCase bare call
  TypeScript:  new Foo()  — new_expression
  JavaScript:  new Foo()  — new_expression
  Go:          Foo{...}  — composite_literal with type_identifier
  Rust:        Foo::new() — scoped_identifier call with name='new'
               Foo { ... } — struct_expression
  Java:        new Foo()  — object_creation_expression
  C#:          new Foo()  — object_creation_expression
  Ruby:        Foo.new    — call where receiver is constant + method is 'new'
  PHP:         new Foo()  — object_creation_expression (name child)
  Swift:       Foo()      — call_expression with simple_identifier callee (PascalCase)
  C:           (no class instantiation — C has no classes, skip)
  C++:         new Foo()  — new_expression
"""

from pathlib import Path

from seam.indexer.graph import Edge, extract_edges

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"


# ── Helpers ────────────────────────────────────────────────────────────────────


def _get_root(parse_fn, path: Path):  # type: ignore[no-untyped-def]
    """Parse path with parse_fn and assert success."""
    root = parse_fn(path)
    assert root is not None, f"Failed to parse {path}"
    return root


def _edges_of_kind(edges: list[Edge], kind: str) -> list[Edge]:
    """Filter edges by kind."""
    return [e for e in edges if e["kind"] == kind]


def _inst_targets(edges: list[Edge]) -> set[str]:
    """Return the set of target names for all instantiates edges."""
    return {e["target"] for e in _edges_of_kind(edges, "instantiates")}


def _call_targets(edges: list[Edge]) -> set[str]:
    """Return the set of target names for all call edges."""
    return {e["target"] for e in _edges_of_kind(edges, "call")}


# ── Python ────────────────────────────────────────────────────────────────────


class TestInstantiatesPython:
    """Python: Foo() PascalCase bare call → instantiates edge."""

    def test_pascal_case_call_emits_instantiates(self, tmp_path: Path) -> None:
        """PascalCase bare call Foo() → instantiates edge with target='Foo'."""
        from seam.indexer.parser import parse_python

        src = tmp_path / "inst.py"
        src.write_text("def builder():\n    x = Foo()\n    return x\n")
        root = _get_root(parse_python, src)
        edges = extract_edges(root, "python", src)
        assert "Foo" in _inst_targets(edges), (
            f"Expected instantiates edge to Foo; got targets={_inst_targets(edges)}"
        )

    def test_lowercase_call_not_instantiates(self, tmp_path: Path) -> None:
        """Lowercase bare call foo() → call edge, NOT instantiates."""
        from seam.indexer.parser import parse_python

        src = tmp_path / "not_inst.py"
        src.write_text("def builder():\n    x = foo()\n    return x\n")
        root = _get_root(parse_python, src)
        edges = extract_edges(root, "python", src)
        assert "foo" not in _inst_targets(edges), "foo() must not be instantiates"
        # foo() should be a call edge
        assert "foo" in _call_targets(edges) or len(edges) == 0, (
            "foo() should produce a call edge or no edges, not instantiates"
        )

    def test_multiple_constructors(self, tmp_path: Path) -> None:
        """Multiple PascalCase calls → multiple instantiates edges."""
        from seam.indexer.parser import parse_python

        src = tmp_path / "multi.py"
        src.write_text(
            "def build():\n"
            "    a = Foo()\n"
            "    b = Bar(1)\n"
            "    c = Baz(x=2)\n"
            "    return a, b, c\n"
        )
        root = _get_root(parse_python, src)
        edges = extract_edges(root, "python", src)
        inst = _inst_targets(edges)
        assert "Foo" in inst
        assert "Bar" in inst
        assert "Baz" in inst

    def test_call_edges_unaffected(self, tmp_path: Path) -> None:
        """Call edges from method calls are unaffected by instantiates detection."""
        from seam.indexer.parser import parse_python

        src = tmp_path / "calls.py"
        src.write_text(
            "def caller():\n"
            "    result = helper()\n"
            "    obj = MyClass()\n"
            "    return result\n"
        )
        root = _get_root(parse_python, src)
        edges = extract_edges(root, "python", src)
        # helper() → call (lowercase)
        assert "helper" in _call_targets(edges)
        # MyClass() → instantiates
        assert "MyClass" in _inst_targets(edges)
        # helper must NOT be in instantiates
        assert "helper" not in _inst_targets(edges)

    def test_never_raises_on_unusual_shape(self, tmp_path: Path) -> None:
        """extract_edges never raises even on edge-case code."""
        from seam.indexer.parser import parse_python

        src = tmp_path / "edge.py"
        # Unusual: nested constructor, attribute call, etc.
        src.write_text(
            "def f():\n"
            "    x = Foo(Bar())\n"
            "    y = a.b.Baz()\n"  # attribute call — not a bare identifier
            "    return x\n"
        )
        root = _get_root(parse_python, src)
        edges = extract_edges(root, "python", src)  # must not raise
        assert isinstance(edges, list)
        # Foo and Bar are bare PascalCase calls
        inst = _inst_targets(edges)
        assert "Foo" in inst
        assert "Bar" in inst


# ── TypeScript / JavaScript ───────────────────────────────────────────────────


class TestInstantiatesTypeScript:
    """TypeScript/JS: new Foo() → instantiates edge."""

    def test_new_expression_emits_instantiates_ts(self, tmp_path: Path) -> None:
        """new Foo() in TS → instantiates edge with target='Foo'."""
        from seam.indexer.parser import parse_typescript

        src = tmp_path / "inst.ts"
        src.write_text(
            "function builder() {\n"
            "  const x = new Foo();\n"
            "  return x;\n"
            "}\n"
        )
        root = _get_root(parse_typescript, src)
        edges = extract_edges(root, "typescript", src)
        assert "Foo" in _inst_targets(edges), (
            f"Expected instantiates->Foo; got {_inst_targets(edges)}"
        )

    def test_new_expression_emits_instantiates_js(self, tmp_path: Path) -> None:
        """new Foo() in JS → instantiates edge with target='Foo'."""
        from seam.indexer.parser import parse_typescript  # JS uses same parser

        src = tmp_path / "inst.js"
        src.write_text(
            "function builder() {\n"
            "  const x = new Bar(1, 2);\n"
            "  return x;\n"
            "}\n"
        )
        root = _get_root(parse_typescript, src)
        edges = extract_edges(root, "javascript", src)
        assert "Bar" in _inst_targets(edges)

    def test_call_expression_not_instantiates_ts(self, tmp_path: Path) -> None:
        """Regular call foo() in TS → NOT instantiates."""
        from seam.indexer.parser import parse_typescript

        src = tmp_path / "call.ts"
        src.write_text(
            "function caller() {\n"
            "  foo();\n"
            "}\n"
        )
        root = _get_root(parse_typescript, src)
        edges = extract_edges(root, "typescript", src)
        assert "foo" not in _inst_targets(edges)

    def test_multiple_new_expressions(self, tmp_path: Path) -> None:
        """Multiple new Foo() calls → multiple instantiates edges."""
        from seam.indexer.parser import parse_typescript

        src = tmp_path / "multi.ts"
        src.write_text(
            "function build() {\n"
            "  const a = new Alpha();\n"
            "  const b = new Beta(x);\n"
            "  return [a, b];\n"
            "}\n"
        )
        root = _get_root(parse_typescript, src)
        edges = extract_edges(root, "typescript", src)
        inst = _inst_targets(edges)
        assert "Alpha" in inst
        assert "Beta" in inst

    def test_call_edges_unaffected_ts(self, tmp_path: Path) -> None:
        """Call edges from regular function calls are preserved alongside instantiates."""
        from seam.indexer.parser import parse_typescript

        src = tmp_path / "mixed.ts"
        src.write_text(
            "function mixed() {\n"
            "  helper();\n"
            "  const obj = new MyClass();\n"
            "}\n"
        )
        root = _get_root(parse_typescript, src)
        edges = extract_edges(root, "typescript", src)
        assert "helper" in _call_targets(edges)
        assert "MyClass" in _inst_targets(edges)
        assert "helper" not in _inst_targets(edges)

    def test_never_raises_ts(self, tmp_path: Path) -> None:
        """extract_edges never raises on unusual shapes."""
        from seam.indexer.parser import parse_typescript

        src = tmp_path / "edge.ts"
        src.write_text(
            "function f() {\n"
            "  const a = new (getFoo())();\n"  # dynamic new — skip gracefully
            "  const b = new Foo();\n"
            "}\n"
        )
        root = _get_root(parse_typescript, src)
        edges = extract_edges(root, "typescript", src)
        assert isinstance(edges, list)
        # Foo must be present even if dynamic new is skipped
        assert "Foo" in _inst_targets(edges)


# ── Go ────────────────────────────────────────────────────────────────────────


class TestInstantiatesGo:
    """Go: Foo{...} composite_literal → instantiates edge."""

    def test_composite_literal_emits_instantiates(self, tmp_path: Path) -> None:
        """Foo{...} → instantiates edge with target='Foo'."""
        from seam.indexer.parser import parse_go

        src = tmp_path / "inst.go"
        src.write_text(
            "package main\n"
            "func builder() interface{} {\n"
            "  x := Foo{Name: \"test\"}\n"
            "  return x\n"
            "}\n"
        )
        root = _get_root(parse_go, src)
        edges = extract_edges(root, "go", src)
        assert "Foo" in _inst_targets(edges), (
            f"Expected instantiates->Foo; got {_inst_targets(edges)}"
        )

    def test_address_of_composite_literal(self, tmp_path: Path) -> None:
        """&Foo{} → instantiates edge with target='Foo'."""
        from seam.indexer.parser import parse_go

        src = tmp_path / "addr.go"
        src.write_text(
            "package main\n"
            "func builder() *Foo {\n"
            "  return &Foo{}\n"
            "}\n"
        )
        root = _get_root(parse_go, src)
        edges = extract_edges(root, "go", src)
        assert "Foo" in _inst_targets(edges)

    def test_call_edges_unaffected_go(self, tmp_path: Path) -> None:
        """Call edges from function calls are preserved."""
        from seam.indexer.parser import parse_go

        src = tmp_path / "mixed.go"
        src.write_text(
            "package main\n"
            "func mixed() {\n"
            "  helper()\n"
            "  x := Repo{Name: \"x\"}\n"
            "  _ = x\n"
            "}\n"
        )
        root = _get_root(parse_go, src)
        edges = extract_edges(root, "go", src)
        assert "helper" in _call_targets(edges)
        assert "Repo" in _inst_targets(edges)

    def test_never_raises_go(self, tmp_path: Path) -> None:
        """extract_edges never raises on unusual Go shapes."""
        from seam.indexer.parser import parse_go

        src = tmp_path / "edge.go"
        src.write_text(
            "package main\n"
            "func f() {\n"
            "  x := Foo{}\n"
            "  _ = x\n"
            "}\n"
        )
        root = _get_root(parse_go, src)
        edges = extract_edges(root, "go", src)
        assert isinstance(edges, list)


# ── Rust ──────────────────────────────────────────────────────────────────────


class TestInstantiatesRust:
    """Rust: Type::new() and Foo{...} → instantiates edges."""

    def test_type_new_call_emits_instantiates(self, tmp_path: Path) -> None:
        """Foo::new() → instantiates edge with target='Foo'."""
        from seam.indexer.parser import parse_rust

        src = tmp_path / "inst.rs"
        src.write_text(
            "fn builder() {\n"
            "    let x = Foo::new();\n"
            "    let _ = x;\n"
            "}\n"
        )
        root = _get_root(parse_rust, src)
        edges = extract_edges(root, "rust", src)
        assert "Foo" in _inst_targets(edges), (
            f"Expected instantiates->Foo; got {_inst_targets(edges)}"
        )

    def test_struct_literal_emits_instantiates(self, tmp_path: Path) -> None:
        """Bar { field: val } struct literal → instantiates edge with target='Bar'."""
        from seam.indexer.parser import parse_rust

        src = tmp_path / "struct.rs"
        src.write_text(
            "fn builder() {\n"
            "    let y = Bar { name: String::new() };\n"
            "    let _ = y;\n"
            "}\n"
        )
        root = _get_root(parse_rust, src)
        edges = extract_edges(root, "rust", src)
        assert "Bar" in _inst_targets(edges)

    def test_other_scoped_call_not_instantiates(self, tmp_path: Path) -> None:
        """Foo::helper() (non-new scoped call) → call edge, NOT instantiates."""
        from seam.indexer.parser import parse_rust

        src = tmp_path / "call.rs"
        src.write_text(
            "fn caller() {\n"
            "    Foo::helper();\n"
            "}\n"
        )
        root = _get_root(parse_rust, src)
        edges = extract_edges(root, "rust", src)
        # Only 'new' triggers instantiates; 'helper' should not
        assert "Foo" not in _inst_targets(edges)

    def test_call_edges_unaffected_rust(self, tmp_path: Path) -> None:
        """Regular call edges are preserved alongside instantiates."""
        from seam.indexer.parser import parse_rust

        src = tmp_path / "mixed.rs"
        src.write_text(
            "fn mixed() {\n"
            "    helper();\n"
            "    let x = Foo::new();\n"
            "    let _ = x;\n"
            "}\n"
        )
        root = _get_root(parse_rust, src)
        edges = extract_edges(root, "rust", src)
        assert "helper" in _call_targets(edges)
        assert "Foo" in _inst_targets(edges)

    def test_never_raises_rust(self, tmp_path: Path) -> None:
        """extract_edges never raises on unusual Rust shapes."""
        from seam.indexer.parser import parse_rust

        src = tmp_path / "edge.rs"
        src.write_text(
            "fn f() {\n"
            "    let x = Foo::new();\n"
            "    let y = Bar { a: 1 };\n"
            "    let _ = (x, y);\n"
            "}\n"
        )
        root = _get_root(parse_rust, src)
        edges = extract_edges(root, "rust", src)
        assert isinstance(edges, list)
        inst = _inst_targets(edges)
        assert "Foo" in inst
        assert "Bar" in inst


# ── Java ──────────────────────────────────────────────────────────────────────


class TestInstantiatesJava:
    """Java: new Foo() → instantiates edge."""

    def test_new_expression_emits_instantiates_java(self, tmp_path: Path) -> None:
        """new Foo() in Java → instantiates edge with target='Foo'."""
        from seam.indexer.parser import parse_java

        src = tmp_path / "Inst.java"
        src.write_text(
            "class Builder {\n"
            "    Object build() {\n"
            "        Foo x = new Foo();\n"
            "        return x;\n"
            "    }\n"
            "}\n"
        )
        root = _get_root(parse_java, src)
        edges = extract_edges(root, "java", src)
        assert "Foo" in _inst_targets(edges)

    def test_call_edges_unaffected_java(self, tmp_path: Path) -> None:
        """Regular method call edges preserved."""
        from seam.indexer.parser import parse_java

        src = tmp_path / "Mixed.java"
        src.write_text(
            "class Mixed {\n"
            "    void run() {\n"
            "        helper();\n"
            "        Foo x = new Foo();\n"
            "    }\n"
            "    void helper() {}\n"
            "}\n"
        )
        root = _get_root(parse_java, src)
        edges = extract_edges(root, "java", src)
        assert "helper" in _call_targets(edges)
        assert "Foo" in _inst_targets(edges)

    def test_never_raises_java(self, tmp_path: Path) -> None:
        """extract_edges never raises on unusual Java shapes."""
        from seam.indexer.parser import parse_java

        src = tmp_path / "Edge.java"
        src.write_text(
            "class Edge {\n"
            "    Object f() {\n"
            "        return new Foo(new Bar());\n"
            "    }\n"
            "}\n"
        )
        root = _get_root(parse_java, src)
        edges = extract_edges(root, "java", src)
        assert isinstance(edges, list)
        inst = _inst_targets(edges)
        assert "Foo" in inst
        assert "Bar" in inst


# ── C# ────────────────────────────────────────────────────────────────────────


class TestInstantiatesCSharp:
    """C#: new Foo() → instantiates edge."""

    def test_new_expression_emits_instantiates_cs(self, tmp_path: Path) -> None:
        """new Foo() in C# → instantiates edge with target='Foo'."""
        from seam.indexer.parser import parse_csharp

        src = tmp_path / "inst.cs"
        src.write_text(
            "class Builder {\n"
            "    object Build() {\n"
            "        Foo x = new Foo();\n"
            "        return x;\n"
            "    }\n"
            "}\n"
        )
        root = _get_root(parse_csharp, src)
        edges = extract_edges(root, "csharp", src)
        assert "Foo" in _inst_targets(edges)

    def test_call_edges_unaffected_cs(self, tmp_path: Path) -> None:
        """Regular method call edges preserved."""
        from seam.indexer.parser import parse_csharp

        src = tmp_path / "mixed.cs"
        src.write_text(
            "class Mixed {\n"
            "    void Run() {\n"
            "        Helper();\n"
            "        Foo x = new Foo();\n"
            "    }\n"
            "    void Helper() {}\n"
            "}\n"
        )
        root = _get_root(parse_csharp, src)
        edges = extract_edges(root, "csharp", src)
        assert "Helper" in _call_targets(edges)
        assert "Foo" in _inst_targets(edges)

    def test_never_raises_cs(self, tmp_path: Path) -> None:
        """extract_edges never raises on unusual C# shapes."""
        from seam.indexer.parser import parse_csharp

        src = tmp_path / "edge.cs"
        src.write_text(
            "class Edge {\n"
            "    object F() { return new Foo(new Bar()); }\n"
            "}\n"
        )
        root = _get_root(parse_csharp, src)
        edges = extract_edges(root, "csharp", src)
        assert isinstance(edges, list)
        inst = _inst_targets(edges)
        assert "Foo" in inst
        assert "Bar" in inst


# ── Ruby ──────────────────────────────────────────────────────────────────────


class TestInstantiatesRuby:
    """Ruby: Foo.new → instantiates edge (constant receiver + method='new')."""

    def test_class_new_emits_instantiates(self, tmp_path: Path) -> None:
        """Foo.new → instantiates edge with target='Foo'."""
        from seam.indexer.parser import parse_ruby

        src = tmp_path / "inst.rb"
        src.write_text(
            "def builder\n"
            "  x = Foo.new\n"
            "  x\n"
            "end\n"
        )
        root = _get_root(parse_ruby, src)
        edges = extract_edges(root, "ruby", src)
        assert "Foo" in _inst_targets(edges)

    def test_lowercase_new_not_instantiates(self, tmp_path: Path) -> None:
        """bar.new (lowercase receiver) → NOT instantiates (not a class)."""
        from seam.indexer.parser import parse_ruby

        src = tmp_path / "not_inst.rb"
        src.write_text(
            "def builder\n"
            "  x = bar.new\n"
            "  x\n"
            "end\n"
        )
        root = _get_root(parse_ruby, src)
        edges = extract_edges(root, "ruby", src)
        # bar.new uses identifier receiver (lowercase) — not a class instantiation
        assert "bar" not in _inst_targets(edges)

    def test_call_edges_unaffected_ruby(self, tmp_path: Path) -> None:
        """Regular call edges preserved (Ruby requires explicit parens for a call node)."""
        from seam.indexer.parser import parse_ruby

        src = tmp_path / "mixed.rb"
        # Ruby: bare identifier 'helper' without () is parsed as an identifier, not a
        # call node. Use 'helper()' to produce a call node in the AST.
        src.write_text(
            "def mixed\n"
            "  helper()\n"
            "  x = Foo.new\n"
            "  x\n"
            "end\n"
        )
        root = _get_root(parse_ruby, src)
        edges = extract_edges(root, "ruby", src)
        assert "helper" in _call_targets(edges)
        assert "Foo" in _inst_targets(edges)
        assert "helper" not in _inst_targets(edges)

    def test_never_raises_ruby(self, tmp_path: Path) -> None:
        """extract_edges never raises on unusual Ruby shapes."""
        from seam.indexer.parser import parse_ruby

        src = tmp_path / "edge.rb"
        src.write_text(
            "def f\n"
            "  x = Foo.new(Bar.new)\n"
            "  x\n"
            "end\n"
        )
        root = _get_root(parse_ruby, src)
        edges = extract_edges(root, "ruby", src)
        assert isinstance(edges, list)
        inst = _inst_targets(edges)
        assert "Foo" in inst
        assert "Bar" in inst


# ── PHP ───────────────────────────────────────────────────────────────────────


class TestInstantiatesPHP:
    """PHP: new Foo() → instantiates edge."""

    def test_new_expression_emits_instantiates_php(self, tmp_path: Path) -> None:
        """new Foo() in PHP → instantiates edge with target='Foo'."""
        from seam.indexer.parser import parse_php

        src = tmp_path / "inst.php"
        src.write_text(
            "<?php\n"
            "function builder() {\n"
            "    $x = new Foo();\n"
            "    return $x;\n"
            "}\n"
        )
        root = _get_root(parse_php, src)
        edges = extract_edges(root, "php", src)
        assert "Foo" in _inst_targets(edges)

    def test_namespaced_new_emits_instantiates_php(self, tmp_path: Path) -> None:
        """new \\NS\\Foo() in PHP → instantiates edge with target='Foo' (last segment)."""
        from seam.indexer.parser import parse_php

        src = tmp_path / "ns_inst.php"
        src.write_text(
            "<?php\n"
            "function builder() {\n"
            "    $x = new \\NS\\Bar();\n"
            "    return $x;\n"
            "}\n"
        )
        root = _get_root(parse_php, src)
        edges = extract_edges(root, "php", src)
        # Namespaced class: emit the last segment 'Bar'
        assert "Bar" in _inst_targets(edges)

    def test_dynamic_new_skipped_php(self, tmp_path: Path) -> None:
        """new $className() with variable class name → skip gracefully (no instantiates)."""
        from seam.indexer.parser import parse_php

        src = tmp_path / "dyn.php"
        src.write_text(
            "<?php\n"
            "function builder($name) {\n"
            "    $x = new $name();\n"
            "    return $x;\n"
            "}\n"
        )
        root = _get_root(parse_php, src)
        edges = extract_edges(root, "php", src)
        # Dynamic new with variable — skip; no instantiates edge expected
        assert isinstance(edges, list)
        # Must not raise; instantiates list may be empty or have other entries
        inst = _inst_targets(edges)
        assert "$name" not in inst
        assert "name" not in inst

    def test_call_edges_unaffected_php(self, tmp_path: Path) -> None:
        """Regular call edges preserved."""
        from seam.indexer.parser import parse_php

        src = tmp_path / "mixed.php"
        src.write_text(
            "<?php\n"
            "function mixed() {\n"
            "    helper();\n"
            "    $x = new Foo();\n"
            "}\n"
            "function helper() {}\n"
        )
        root = _get_root(parse_php, src)
        edges = extract_edges(root, "php", src)
        assert "helper" in _call_targets(edges)
        assert "Foo" in _inst_targets(edges)

    def test_never_raises_php(self, tmp_path: Path) -> None:
        """extract_edges never raises on unusual PHP shapes."""
        from seam.indexer.parser import parse_php

        src = tmp_path / "edge.php"
        src.write_text(
            "<?php\n"
            "function f() {\n"
            "    return new Foo(new Bar());\n"
            "}\n"
        )
        root = _get_root(parse_php, src)
        edges = extract_edges(root, "php", src)
        assert isinstance(edges, list)
        inst = _inst_targets(edges)
        assert "Foo" in inst
        assert "Bar" in inst


# ── Swift ─────────────────────────────────────────────────────────────────────


class TestInstantiatesSwift:
    """Swift: Foo() PascalCase call_expression → instantiates edge."""

    def test_pascal_case_call_emits_instantiates_swift(self, tmp_path: Path) -> None:
        """PascalCase Foo() in Swift → instantiates edge."""
        from seam.indexer.parser import parse_swift

        src = tmp_path / "inst.swift"
        src.write_text(
            "class Builder {\n"
            "    func build() -> Foo {\n"
            "        let x = Foo()\n"
            "        return x\n"
            "    }\n"
            "}\n"
        )
        root = _get_root(parse_swift, src)
        edges = extract_edges(root, "swift", src)
        assert "Foo" in _inst_targets(edges)

    def test_lowercase_call_not_instantiates_swift(self, tmp_path: Path) -> None:
        """Lowercase call foo() in Swift → NOT instantiates."""
        from seam.indexer.parser import parse_swift

        src = tmp_path / "not_inst.swift"
        src.write_text(
            "func caller() {\n"
            "    foo()\n"
            "}\n"
            "func foo() {}\n"
        )
        root = _get_root(parse_swift, src)
        edges = extract_edges(root, "swift", src)
        assert "foo" not in _inst_targets(edges)

    def test_call_edges_unaffected_swift(self, tmp_path: Path) -> None:
        """Regular call edges preserved alongside instantiates."""
        from seam.indexer.parser import parse_swift

        src = tmp_path / "mixed.swift"
        src.write_text(
            "func mixed() {\n"
            "    helper()\n"
            "    let obj = MyClass()\n"
            "}\n"
            "func helper() {}\n"
        )
        root = _get_root(parse_swift, src)
        edges = extract_edges(root, "swift", src)
        assert "helper" in _call_targets(edges)
        assert "MyClass" in _inst_targets(edges)

    def test_never_raises_swift(self, tmp_path: Path) -> None:
        """extract_edges never raises on unusual Swift shapes."""
        from seam.indexer.parser import parse_swift

        src = tmp_path / "edge.swift"
        src.write_text(
            "func f() {\n"
            "    let x = Foo(a: Bar())\n"
            "    _ = x\n"
            "}\n"
        )
        root = _get_root(parse_swift, src)
        edges = extract_edges(root, "swift", src)
        assert isinstance(edges, list)
        inst = _inst_targets(edges)
        assert "Foo" in inst
        assert "Bar" in inst


# ── C++ ───────────────────────────────────────────────────────────────────────


class TestInstantiatesCpp:
    """C++: new Foo() → instantiates edge."""

    def test_new_expression_emits_instantiates_cpp(self, tmp_path: Path) -> None:
        """new Foo() in C++ → instantiates edge."""
        from seam.indexer.parser import parse_cpp

        src = tmp_path / "inst.cpp"
        src.write_text(
            "void doIt() {\n"
            "    Foo* x = new Foo();\n"
            "    delete x;\n"
            "}\n"
        )
        root = _get_root(parse_cpp, src)
        edges = extract_edges(root, "cpp", src)
        assert "Foo" in _inst_targets(edges)

    def test_call_edges_unaffected_cpp(self, tmp_path: Path) -> None:
        """Regular call edges preserved."""
        from seam.indexer.parser import parse_cpp

        src = tmp_path / "mixed.cpp"
        src.write_text(
            "void doIt() {\n"
            "    helper();\n"
            "    Foo* x = new Foo();\n"
            "    delete x;\n"
            "}\n"
            "void helper() {}\n"
        )
        root = _get_root(parse_cpp, src)
        edges = extract_edges(root, "cpp", src)
        assert "helper" in _call_targets(edges)
        assert "Foo" in _inst_targets(edges)

    def test_never_raises_cpp(self, tmp_path: Path) -> None:
        """extract_edges never raises on unusual C++ shapes."""
        from seam.indexer.parser import parse_cpp

        src = tmp_path / "edge.cpp"
        src.write_text(
            "void f() {\n"
            "    Foo* x = new Foo(new Bar());\n"
            "    delete x;\n"
            "}\n"
        )
        root = _get_root(parse_cpp, src)
        edges = extract_edges(root, "cpp", src)
        assert isinstance(edges, list)
        inst = _inst_targets(edges)
        assert "Foo" in inst
        assert "Bar" in inst


# ── C — no instantiates ───────────────────────────────────────────────────────


class TestInstantiatesC:
    """C: no class instantiation — instantiates edges must not be emitted."""

    def test_no_instantiates_in_c(self, tmp_path: Path) -> None:
        """C has no classes; no instantiates edges should be emitted."""
        from seam.indexer.parser import parse_c

        src = tmp_path / "inst.c"
        src.write_text(
            "void doIt() {\n"
            "    helper();\n"
            "}\n"
            "void helper() {}\n"
        )
        root = _get_root(parse_c, src)
        edges = extract_edges(root, "c", src)
        assert _inst_targets(edges) == set(), (
            f"C should not emit instantiates; got {_inst_targets(edges)}"
        )


# ── Edge kind vocabulary ───────────────────────────────────────────────────────


class TestInstantiatesKindVocabulary:
    """Instantiates kind is a valid edge kind string in the vocabulary."""

    def test_instantiates_kind_in_extract_edges_python(self, tmp_path: Path) -> None:
        """Emitted instantiates edge has kind='instantiates' (not misspelled)."""
        from seam.indexer.parser import parse_python

        src = tmp_path / "vocab.py"
        src.write_text("def f():\n    x = MyClass()\n    return x\n")
        root = _get_root(parse_python, src)
        edges = extract_edges(root, "python", src)
        inst_edges = _edges_of_kind(edges, "instantiates")
        assert len(inst_edges) > 0, "Expected at least one instantiates edge"
        for e in inst_edges:
            assert e["kind"] == "instantiates"
            assert e["target"] == "MyClass"

    def test_instantiates_kind_in_extract_edges_ts(self, tmp_path: Path) -> None:
        """TS new_expression emits kind='instantiates' exactly."""
        from seam.indexer.parser import parse_typescript

        src = tmp_path / "vocab.ts"
        src.write_text("function f() { const x = new MyClass(); }\n")
        root = _get_root(parse_typescript, src)
        edges = extract_edges(root, "typescript", src)
        inst_edges = _edges_of_kind(edges, "instantiates")
        assert len(inst_edges) > 0
        assert all(e["kind"] == "instantiates" for e in inst_edges)
        assert all(e["target"] == "MyClass" for e in inst_edges)
