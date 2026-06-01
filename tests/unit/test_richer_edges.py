"""Tests for Phase 1 / issue #4 — Richer edge extraction.

Covers three gaps hardened in this slice:

  A — Arrow-function call sites (TypeScript/JS):
        A1  const-assigned arrow function produces a call edge sourced from
            the variable name (e.g. `const handler = () => { foo() }` →
            source='handler', target='foo').
        A2  Arrow function nested inside a named function uses the arrow's
            own variable name, not the outer function name, when the arrow
            is assigned to a const.
        A3  Anonymous arrow function (not assigned to a named variable)
            propagates to the nearest named enclosing scope; if none, no edge.

  B — Namespace imports (TypeScript/JS):
        B1  `import * as ns from 'mod'` produces an import edge with
            target = 'ns' (the local alias), consistent with how default
            imports use the local binding as target.
        B2  The namespace alias (not the module path string) is the target.

  C — Aliased imports (TypeScript/JS):
        C1  `import { a as b } from 'mod'` produces an import edge with
            target = 'a' (the real exported name), NOT 'b' (the alias).
        C2  `import { realName } from 'mod'` (no alias) still works correctly.

  D — Python aliased imports (alignment verification):
        D1  `import os as X` → target = 'os' (real module name).
        D2  `from pathlib import Path as P` → target = 'Path' (real name).

Style: inline minimal source, temp files, no fixture-file counts affected.
Follows test_confidence.py / test_hardening.py conventions.
"""

from pathlib import Path

from seam.indexer.graph import extract_edges, extract_symbols
from seam.indexer.parser import parse_python, parse_typescript

# ── Fixture helpers ──────────────────────────────────────────────────────────


def _ts_file(tmp_path: Path, content: str) -> Path:
    """Write a temp .ts file and return its path."""
    p = tmp_path / f"t{id(content)}.ts"
    p.write_text(content)
    return p


def _py_file(tmp_path: Path, content: str) -> Path:
    """Write a temp .py file and return its path."""
    p = tmp_path / f"p{id(content)}.py"
    p.write_text(content)
    return p


# ── A: Arrow-function call sites ─────────────────────────────────────────────


class TestArrowFunctionCallEdges:
    """A: Calls inside arrow-function bodies must produce call edges."""

    def test_top_level_arrow_produces_call_edge(self, tmp_path: Path) -> None:
        """A1: `const handler = () => { foo() }` → call edge source='handler', target='foo'."""
        src = _ts_file(tmp_path, "const handler = () => { foo(); };\n")
        root = parse_typescript(src)
        assert root is not None

        edges = extract_edges(root, "typescript", src)
        call_edges = [e for e in edges if e["kind"] == "call" and e["target"] == "foo"]
        assert call_edges, "expected a call edge to 'foo' from inside the arrow function"
        assert call_edges[0]["source"] == "handler", (
            f"source should be 'handler' (the variable name), got {call_edges[0]['source']!r}"
        )

    def test_arrow_inside_named_function_uses_enclosing_function(
        self, tmp_path: Path
    ) -> None:
        """A2 (regression-fix): Arrow assigned to const INSIDE a named function — the enclosing
        named function wins over the arrow const name.

        `function outer() { const inner = () => { bar(); }; }`
        → source should be 'outer', NOT 'inner'.

        The named function_declaration always beats any inner arrow const name.
        """
        src = _ts_file(
            tmp_path,
            "function outer() { const inner = () => { bar(); }; }\n",
        )
        root = parse_typescript(src)
        assert root is not None

        edges = extract_edges(root, "typescript", src)
        call_edges = [e for e in edges if e["kind"] == "call" and e["target"] == "bar"]
        assert call_edges, "expected a call edge to 'bar' from inside the nested arrow"
        assert call_edges[0]["source"] == "outer", (
            f"source should be 'outer' (enclosing named function wins), got {call_edges[0]['source']!r}"
        )

    def test_anonymous_arrow_in_named_function_attributes_to_outer(
        self, tmp_path: Path
    ) -> None:
        """A3: An arrow passed as argument (no assignment) falls back to the enclosing function."""
        # The arrow is not assigned to any variable; it's inline in a call arg.
        src = _ts_file(
            tmp_path,
            "function outerFn() { [1,2].forEach(() => { baz(); }); }\n",
        )
        root = parse_typescript(src)
        assert root is not None

        edges = extract_edges(root, "typescript", src)
        call_edges = [e for e in edges if e["kind"] == "call" and e["target"] == "baz"]
        # The anonymous arrow has no const name; walk up finds outerFn.
        assert call_edges, "expected a call edge to 'baz' attributed to the enclosing function"
        assert call_edges[0]["source"] == "outerFn", (
            f"source should be 'outerFn' (enclosing function), got {call_edges[0]['source']!r}"
        )

    def test_arrow_call_edge_confidence_is_inferred(self, tmp_path: Path) -> None:
        """A1-conf: Call edge from arrow function body must have confidence='INFERRED'
        (upgraded to EXTRACTED only if the target is a same-file symbol — verified separately)."""
        src = _ts_file(tmp_path, "const h = () => { externalFn(); };\n")
        root = parse_typescript(src)
        assert root is not None

        edges = extract_edges(root, "typescript", src)
        call_edges = [e for e in edges if e["kind"] == "call"]
        assert call_edges
        assert call_edges[0]["confidence"] == "INFERRED"

    def test_arrow_call_edge_extracted_when_target_is_local_symbol(
        self, tmp_path: Path
    ) -> None:
        """A1-conf-extracted: When the target is a unique same-file symbol, confidence='EXTRACTED'."""
        src = _ts_file(
            tmp_path,
            "function helper(): void {}\nconst caller = () => { helper(); };\n",
        )
        root = parse_typescript(src)
        assert root is not None

        symbols = extract_symbols(root, "typescript", src)
        edges = extract_edges(root, "typescript", src, symbols=symbols)
        call_edges = [
            e for e in edges if e["kind"] == "call" and e["target"] == "helper"
        ]
        assert call_edges, "expected a call edge from the arrow to helper"
        assert call_edges[0]["source"] == "caller"
        assert call_edges[0]["confidence"] == "EXTRACTED", (
            "target 'helper' is a unique same-file symbol → should be EXTRACTED"
        )


# ── A-reg: Attribution regression tests (FIX 1 — fallback semantics) ─────────


class TestArrowAttributionRegression:
    """Regression tests locking in the three attribution traces from FIX 1.

    These tests ensure that:
      (1) top-level const arrow   → arrow const name is used as source (fallback path)
      (2) named function > arrow  → named function wins, arrow const name is NOT used
      (3) named method > arrow    → qualified method name wins, arrow const name is NOT used
    """

    def test_top_level_const_arrow_uses_const_name(self, tmp_path: Path) -> None:
        """Trace 1: `const handler = () => { foo() }` → source='handler'.

        A top-level const-assigned arrow has no enclosing named function/method,
        so the arrow's variable name is returned as the fallback.
        """
        src = _ts_file(tmp_path, "const handler = () => { foo(); };\n")
        root = parse_typescript(src)
        assert root is not None

        edges = extract_edges(root, "typescript", src)
        call_edges = [e for e in edges if e["kind"] == "call" and e["target"] == "foo"]
        assert call_edges, "expected a call edge to 'foo'"
        assert call_edges[0]["source"] == "handler", (
            f"top-level arrow: source should be 'handler', got {call_edges[0]['source']!r}"
        )

    def test_named_function_beats_inner_arrow_const(self, tmp_path: Path) -> None:
        """Trace 2: `function outer(){ const x = () => { foo() } }` → source='outer', NOT 'x'.

        The enclosing named function_declaration must win over the inner arrow's const name.
        """
        src = _ts_file(
            tmp_path,
            "function outer() { const x = () => { foo(); }; }\n",
        )
        root = parse_typescript(src)
        assert root is not None

        edges = extract_edges(root, "typescript", src)
        call_edges = [e for e in edges if e["kind"] == "call" and e["target"] == "foo"]
        assert call_edges, "expected a call edge to 'foo'"
        assert call_edges[0]["source"] == "outer", (
            f"named function must win over inner arrow: expected 'outer', got {call_edges[0]['source']!r}"
        )
        assert call_edges[0]["source"] != "x", (
            "arrow const name 'x' must NOT be used when an enclosing named function exists"
        )

    def test_named_method_beats_inner_arrow_const(self, tmp_path: Path) -> None:
        """Trace 3: `class C { m(){ const x = () => { foo() } } }` → source='C.m', NOT 'x'.

        The enclosing method_definition (qualified with class name) must win over
        the inner arrow's const name.
        """
        src = _ts_file(
            tmp_path,
            "class C { m() { const x = () => { foo(); }; } }\n",
        )
        root = parse_typescript(src)
        assert root is not None

        edges = extract_edges(root, "typescript", src)
        call_edges = [e for e in edges if e["kind"] == "call" and e["target"] == "foo"]
        assert call_edges, "expected a call edge to 'foo'"
        assert call_edges[0]["source"] == "C.m", (
            f"class method must win over inner arrow: expected 'C.m', got {call_edges[0]['source']!r}"
        )
        assert call_edges[0]["source"] != "x", (
            "arrow const name 'x' must NOT be used when an enclosing named method exists"
        )


# ── B: Namespace imports ──────────────────────────────────────────────────────


class TestNamespaceImports:
    """B: `import * as ns from 'mod'` must produce an import edge."""

    def test_namespace_import_produces_edge(self, tmp_path: Path) -> None:
        """B1: `import * as ns from 'mod'` → import edge with target='ns'."""
        src = _ts_file(tmp_path, "import * as ns from 'somemod';\n")
        root = parse_typescript(src)
        assert root is not None

        edges = extract_edges(root, "typescript", src)
        import_edges = [e for e in edges if e["kind"] == "import"]
        assert import_edges, "expected an import edge for `import * as ns from 'somemod'`"
        targets = {e["target"] for e in import_edges}
        assert "ns" in targets, (
            f"expected 'ns' (the namespace alias) as the import target, got: {targets}"
        )

    def test_namespace_import_target_is_alias_not_module_path(
        self, tmp_path: Path
    ) -> None:
        """B2: The edge target must be 'ns', not the raw module path 'somemod'."""
        src = _ts_file(tmp_path, "import * as utils from 'my-utils';\n")
        root = parse_typescript(src)
        assert root is not None

        edges = extract_edges(root, "typescript", src)
        import_edges = [e for e in edges if e["kind"] == "import"]
        targets = {e["target"] for e in import_edges}
        # Must have the alias, must NOT have the module path as a bare target
        assert "utils" in targets, f"expected 'utils' in targets, got {targets}"
        assert "my-utils" not in targets, "module path 'my-utils' must not be a target"

    def test_namespace_import_source_is_file_stem(self, tmp_path: Path) -> None:
        """B-source: Namespace import edge source must be the file stem."""
        src = _ts_file(tmp_path, "import * as ns from 'somemod';\n")
        root = parse_typescript(src)
        assert root is not None

        edges = extract_edges(root, "typescript", src)
        import_edges = [e for e in edges if e["kind"] == "import"]
        assert import_edges
        assert import_edges[0]["source"] == src.stem

    def test_namespace_import_confidence_is_inferred(self, tmp_path: Path) -> None:
        """B-conf: Namespace import edge must have confidence='INFERRED'."""
        src = _ts_file(tmp_path, "import * as ns from 'somemod';\n")
        root = parse_typescript(src)
        assert root is not None

        edges = extract_edges(root, "typescript", src)
        import_edges = [e for e in edges if e["kind"] == "import"]
        assert import_edges
        assert all(e["confidence"] == "INFERRED" for e in import_edges)


# ── C: Aliased imports ────────────────────────────────────────────────────────


class TestAliasedImports:
    """C: `import { a as b }` must produce an import edge with target='a' (real name)."""

    def test_aliased_import_target_is_real_name(self, tmp_path: Path) -> None:
        """C1: `import { origName as localAlias }` → target = 'origName', not 'localAlias'."""
        src = _ts_file(tmp_path, "import { origName as localAlias } from 'mod';\n")
        root = parse_typescript(src)
        assert root is not None

        edges = extract_edges(root, "typescript", src)
        import_edges = [e for e in edges if e["kind"] == "import"]
        assert import_edges, "expected an import edge for aliased import"
        targets = {e["target"] for e in import_edges}
        assert "origName" in targets, (
            f"expected 'origName' (real exported name) in targets, got: {targets}"
        )
        assert "localAlias" not in targets, (
            "local alias 'localAlias' must NOT appear as the edge target"
        )

    def test_aliased_import_multiple_specifiers(self, tmp_path: Path) -> None:
        """C1-multi: `import { a as b, c as d }` → targets are 'a' and 'c'."""
        src = _ts_file(tmp_path, "import { alpha as a, beta as b } from 'mod';\n")
        root = parse_typescript(src)
        assert root is not None

        edges = extract_edges(root, "typescript", src)
        import_edges = [e for e in edges if e["kind"] == "import"]
        targets = {e["target"] for e in import_edges}
        assert "alpha" in targets, f"expected 'alpha' in targets, got {targets}"
        assert "beta" in targets, f"expected 'beta' in targets, got {targets}"
        assert "a" not in targets, "alias 'a' must not be a target"
        assert "b" not in targets, "alias 'b' must not be a target"

    def test_non_aliased_named_import_unchanged(self, tmp_path: Path) -> None:
        """C2: `import { realName }` (no alias) → target = 'realName' as before."""
        src = _ts_file(tmp_path, "import { realName } from 'mod';\n")
        root = parse_typescript(src)
        assert root is not None

        edges = extract_edges(root, "typescript", src)
        import_edges = [e for e in edges if e["kind"] == "import"]
        targets = {e["target"] for e in import_edges}
        assert "realName" in targets, f"expected 'realName' in targets, got {targets}"

    def test_aliased_import_confidence_is_inferred(self, tmp_path: Path) -> None:
        """C-conf: Aliased import edge must have confidence='INFERRED'."""
        src = _ts_file(tmp_path, "import { foo as bar } from 'mod';\n")
        root = parse_typescript(src)
        assert root is not None

        edges = extract_edges(root, "typescript", src)
        import_edges = [e for e in edges if e["kind"] == "import"]
        assert import_edges
        assert all(e["confidence"] == "INFERRED" for e in import_edges)


# ── D: Python aliased import alignment ───────────────────────────────────────


class TestPythonAliasedImports:
    """D: Python aliased imports already resolve to the real name — verify alignment."""

    def test_import_aliased_module_uses_real_name(self, tmp_path: Path) -> None:
        """D1: `import os as operating_system` → target = 'os'."""
        src = _py_file(tmp_path, "import os as operating_system\n")
        root = parse_python(src)
        assert root is not None

        edges = extract_edges(root, "python", src)
        import_edges = [e for e in edges if e["kind"] == "import"]
        assert import_edges
        targets = {e["target"] for e in import_edges}
        assert "os" in targets, f"expected 'os' in targets, got {targets}"
        assert "operating_system" not in targets, (
            "alias 'operating_system' must NOT appear as the edge target"
        )

    def test_from_import_aliased_name_uses_real_name(self, tmp_path: Path) -> None:
        """D2: `from pathlib import Path as P` → target = 'Path'."""
        src = _py_file(tmp_path, "from pathlib import Path as P\n")
        root = parse_python(src)
        assert root is not None

        edges = extract_edges(root, "python", src)
        import_edges = [e for e in edges if e["kind"] == "import"]
        assert import_edges
        targets = {e["target"] for e in import_edges}
        assert "Path" in targets, f"expected 'Path' in targets, got {targets}"
        assert "P" not in targets, "alias 'P' must NOT appear as the edge target"
