"""Integration test for P6a — inheritance edges in impact traversal.

GOAL: an interface/base-class change must surface its subclasses/implementers
in seam_impact. Inheritance edges are string-name-keyed (source=subclass,
target=base), so upstream traversal from a base class reaches its subclasses.

These tests build a real indexed DB with inheritance edges and verify:
    INH1 — impact(Base, upstream) surfaces the subclass as a WILL_BREAK dependent.
    INH2 — pure call/import traversal is unchanged (call-only edge still reached).
"""

from pathlib import Path

from seam.analysis.impact import impact
from seam.indexer.db import init_db, upsert_file
from seam.indexer.graph import Edge, Symbol


def _sym(name: str, file: str, line: int = 1) -> Symbol:
    return Symbol(
        name=name, kind="class", file=file,
        start_line=line, end_line=line + 2,
        docstring=None, signature=f"class {name}",
        decorators=[], is_exported=True,
        visibility="public", qualified_name=name,
    )


def _edge(source: str, target: str, kind: str, file: str) -> Edge:
    return Edge(source=source, target=target, kind=kind,
                file=file, line=1, confidence="INFERRED")


def _make_db(tmp_path: Path):  # type: ignore[no-untyped-def]
    """Index a base class Base with a subclass Sub (Sub extends Base)."""
    db_path = tmp_path / ".seam" / "seam.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = init_db(db_path)

    src = tmp_path / "models.py"
    src.write_text("class Base: pass\nclass Sub(Base): pass\ndef caller(): helper()\n")

    symbols = [_sym("Base", str(src), 1), _sym("Sub", str(src), 2)]
    edges = [
        _edge("Sub", "Base", "extends", str(src)),
        # An independent call edge to prove call traversal is unaffected.
        _edge("caller", "helper", "call", str(src)),
    ]
    upsert_file(conn, src, "python", "abc", symbols, edges)
    conn.commit()
    return conn


def _names(tier_group: dict) -> set[str]:  # type: ignore[type-arg]
    return {e["name"] for entries in tier_group.values() for e in entries}


class TestInheritanceImpact:
    def test_base_change_surfaces_subclass(self, tmp_path: Path) -> None:
        """INH1: impact on Base (upstream) lists Sub as a direct dependent."""
        conn = _make_db(tmp_path)
        result = impact(conn, "Base", direction="upstream", max_depth=3)
        assert result["found"] is True
        names = _names(result["upstream"])
        assert "Sub" in names, (
            f"subclass Sub not surfaced as a dependent of Base; got {names}"
        )
        # It is a DIRECT (1-hop) dependent → WILL_BREAK tier.
        will_break = {e["name"] for e in result["upstream"]["WILL_BREAK"]}
        assert "Sub" in will_break

    def test_call_traversal_unaffected(self, tmp_path: Path) -> None:
        """INH2: the call edge caller→helper still resolves in downstream traversal."""
        conn = _make_db(tmp_path)
        result = impact(conn, "caller", direction="downstream", max_depth=3)
        names = _names(result["downstream"])
        assert "helper" in names, f"call traversal regressed; got {names}"

    def test_subclass_downstream_reaches_base(self, tmp_path: Path) -> None:
        """Downstream from Sub reaches Base (Sub depends on Base)."""
        conn = _make_db(tmp_path)
        result = impact(conn, "Sub", direction="downstream", max_depth=3)
        names = _names(result["downstream"])
        assert "Base" in names, f"Sub should depend on Base; got {names}"
