"""Tests for P6a — inheritance edges (kind='extends' / kind='implements').

A class's base-class / interface clauses are extracted as string-name-keyed
edges so that an interface/base-class change surfaces its subclasses/implementers
in seam_impact (the subclass DEPENDS on the base → upstream traversal from Base
reaches the subclass).

Edge direction contract (matches call/import edges):
    source = the subclass / implementer NAME
    target = the base class / interface NAME
    kind   = 'extends' (superclass) | 'implements' (interface)

These tests are written FIRST (TDD). They fail until the extractors emit the
new edge kinds.
"""

from pathlib import Path

from seam.indexer.graph import Edge, extract_edges
from seam.indexer.parser import (
    parse_csharp,
    parse_java,
    parse_python,
    parse_typescript,
)


def _parse(parsefn, code: str, suffix: str, tmp_path: Path):  # type: ignore[no-untyped-def]
    """Write inline source to a temp file, parse it, return the root node."""
    src = tmp_path / f"sample{suffix}"
    src.write_text(code)
    root = parsefn(src)
    assert root is not None, f"failed to parse {suffix} source"
    return root, src


def _edge(edges: list[Edge], *, source: str, target: str, kind: str) -> Edge | None:
    """Find an edge matching source/target/kind, or None."""
    for e in edges:
        if e["source"] == source and e["target"] == target and e["kind"] == kind:
            return e
    return None


# ── Python: subclass(Base) → extends ─────────────────────────────────────────


class TestPythonInheritance:
    def test_subclass_emits_extends_edge(self, tmp_path: Path) -> None:
        """class Sub(Base): ... → extends edge Sub→Base."""
        root, src = _parse(parse_python, "class Sub(Base):\n    pass\n", ".py", tmp_path)
        edges = extract_edges(root, "python", src)
        assert _edge(edges, source="Sub", target="Base", kind="extends") is not None, (
            f"no extends edge Sub→Base; edges={[(e['source'], e['target'], e['kind']) for e in edges]}"
        )

    def test_multiple_bases_each_emit_extends(self, tmp_path: Path) -> None:
        """class Sub(Base, Mixin): ... → one extends edge per base."""
        root, src = _parse(parse_python, "class Sub(Base, Mixin):\n    pass\n", ".py", tmp_path)
        edges = extract_edges(root, "python", src)
        assert _edge(edges, source="Sub", target="Base", kind="extends") is not None
        assert _edge(edges, source="Sub", target="Mixin", kind="extends") is not None

    def test_no_base_emits_no_inheritance_edge(self, tmp_path: Path) -> None:
        """class Lonely: ... → no extends/implements edges."""
        root, src = _parse(parse_python, "class Lonely:\n    pass\n", ".py", tmp_path)
        edges = extract_edges(root, "python", src)
        inh = [e for e in edges if e["kind"] in ("extends", "implements")]
        assert inh == [], f"expected no inheritance edges, got {inh}"


# ── TypeScript: implements / extends ─────────────────────────────────────────


class TestTypeScriptInheritance:
    def test_class_implements_emits_implements_edge(self, tmp_path: Path) -> None:
        """class C implements Iface {} → implements edge C→Iface."""
        root, src = _parse(parse_typescript, "class C implements Iface {}\n", ".ts", tmp_path)
        edges = extract_edges(root, "typescript", src)
        assert _edge(edges, source="C", target="Iface", kind="implements") is not None, (
            f"no implements edge C→Iface; edges={[(e['source'], e['target'], e['kind']) for e in edges]}"
        )

    def test_class_extends_emits_extends_edge(self, tmp_path: Path) -> None:
        """class C extends Base {} → extends edge C→Base."""
        root, src = _parse(parse_typescript, "class C extends Base {}\n", ".ts", tmp_path)
        edges = extract_edges(root, "typescript", src)
        assert _edge(edges, source="C", target="Base", kind="extends") is not None

    def test_generic_base_normalized_to_bare_name(self, tmp_path: Path) -> None:
        """class C extends Base<T> {} → extends edge C→Base (generic args stripped)."""
        root, src = _parse(parse_typescript, "class C extends Base<T> {}\n", ".ts", tmp_path)
        edges = extract_edges(root, "typescript", src)
        assert _edge(edges, source="C", target="Base", kind="extends") is not None

    def test_interface_extends_emits_extends_edge(self, tmp_path: Path) -> None:
        """interface A extends B {} → extends edge A→B."""
        root, src = _parse(parse_typescript, "interface A extends B {}\n", ".ts", tmp_path)
        edges = extract_edges(root, "typescript", src)
        assert _edge(edges, source="A", target="B", kind="extends") is not None


# ── Java: extends / implements ───────────────────────────────────────────────


class TestJavaInheritance:
    def test_class_extends_and_implements(self, tmp_path: Path) -> None:
        """class Sub extends Base implements Iface {} → both edge kinds."""
        root, src = _parse(
            parse_java, "class Sub extends Base implements Iface {}\n", ".java", tmp_path
        )
        edges = extract_edges(root, "java", src)
        assert _edge(edges, source="Sub", target="Base", kind="extends") is not None
        assert _edge(edges, source="Sub", target="Iface", kind="implements") is not None

    def test_generic_superclass_normalized(self, tmp_path: Path) -> None:
        """class G extends Base<T> {} → extends edge G→Base."""
        root, src = _parse(parse_java, "class G extends Base<T> {}\n", ".java", tmp_path)
        edges = extract_edges(root, "java", src)
        assert _edge(edges, source="G", target="Base", kind="extends") is not None

    def test_interface_extends_emits_extends(self, tmp_path: Path) -> None:
        """interface I2 extends Iface {} → extends edge I2→Iface."""
        root, src = _parse(parse_java, "interface I2 extends Iface {}\n", ".java", tmp_path)
        edges = extract_edges(root, "java", src)
        assert _edge(edges, source="I2", target="Iface", kind="extends") is not None


# ── C#: base_list (no syntactic class/interface split → all 'extends') ────────


class TestCSharpInheritance:
    def test_base_list_emits_extends_edges(self, tmp_path: Path) -> None:
        """class Sub : Base, IFace {} → extends edges to each base entry."""
        root, src = _parse(parse_csharp, "class Sub : Base, IFace {}\n", ".cs", tmp_path)
        edges = extract_edges(root, "csharp", src)
        assert _edge(edges, source="Sub", target="Base", kind="extends") is not None
        assert _edge(edges, source="Sub", target="IFace", kind="extends") is not None

    def test_qualified_and_generic_base_normalized(self, tmp_path: Path) -> None:
        """class Sub : Ns.Base, IFace<T> {} → bare names Base / IFace."""
        root, src = _parse(parse_csharp, "class Sub : Ns.Base, IFace<T> {}\n", ".cs", tmp_path)
        edges = extract_edges(root, "csharp", src)
        assert _edge(edges, source="Sub", target="Base", kind="extends") is not None
        assert _edge(edges, source="Sub", target="IFace", kind="extends") is not None


# ── Call/import edges remain unaffected by the new kinds ─────────────────────


class TestExistingEdgesUnaffected:
    def test_python_call_and_import_still_present(self, tmp_path: Path) -> None:
        """Adding inheritance extraction must not drop call/import edges."""
        code = "import os\n\n\nclass Sub(Base):\n    def run(self):\n        helper()\n"
        root, src = _parse(parse_python, code, ".py", tmp_path)
        edges = extract_edges(root, "python", src)
        assert any(e["kind"] == "import" and e["target"] == "os" for e in edges)
        assert any(e["kind"] == "call" and e["target"] == "helper" for e in edges)
        assert _edge(edges, source="Sub", target="Base", kind="extends") is not None
