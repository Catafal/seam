"""Completion of read-time receiver-type resolution (post-Tier-B gap fixes G1 + G2).

Tier B made call edges carry qualified ``Type.method`` targets, and Tier A bridged
bare<->qualified for ``context()``. Two surfaces were left behind — this module locks
them down:

  G1 — ``impact``/``trace`` did NOT resolve a BARE METHOD name to its qualified
       ``Class.method`` definition the way ``context()`` does. A method is stored
       only as ``Class.method`` (no bare symbol), so ``seam impact speakText`` returned
       ``found=false`` with an empty blast radius even though callers exist. Top-level
       FUNCTIONS were unaffected (they are stored bare), which is why Python looked fine
       and Swift/OOP code did not.  Fix: ``expand_impact_seeds`` now suffix-resolves a
       bare name with no exact symbol to its qualified def(s), and ``impact`` derives
       ``found`` over the expanded seeds.

  G2 — Swift ``Self`` (capital metatype keyword) was NOT normalized to the enclosing
       class. ``Self.parse()`` produced a bogus edge target ``Self.parse`` (a type
       literally named "Self") instead of ``EnclosingClass.parse``. Fix: normalize
       ``Self`` exactly like lowercase ``self`` in the receiver-type resolver.
"""

import os
import sqlite3
import tempfile
from pathlib import Path

from seam.analysis.impact import impact
from seam.indexer.db import init_db, upsert_file
from seam.indexer.graph import Edge, Symbol, extract_edges
from seam.query.names import expand_impact_seeds

# ── shared seeding helpers (mirror tests/unit/test_names.py) ──────────────────


def _sym(name: str, kind: str) -> Symbol:
    return Symbol(name=name, kind=kind, file="x", start_line=1, end_line=5, docstring=None)


def _edge(source: str, target: str) -> Edge:
    return Edge(
        source=source, target=target, kind="call", file="x", line=10, confidence="EXTRACTED"
    )


def _seed_db(symbols: list[Symbol], edges: list[Edge]) -> sqlite3.Connection:
    conn = init_db(Path(":memory:"))
    with tempfile.NamedTemporaryFile(suffix=".swift", delete=False) as f:
        filepath = Path(f.name)
        f.write(b"// seam test\n")
    try:
        syms = [
            Symbol(
                name=s["name"],
                kind=s["kind"],
                file=str(filepath),
                start_line=s["start_line"],
                end_line=s["end_line"],
                docstring=None,
            )
            for s in symbols
        ]
        eds = [
            Edge(
                source=e["source"],
                target=e["target"],
                kind=e["kind"],
                file=str(filepath),
                line=e["line"],
                confidence=e.get("confidence", "EXTRACTED"),
            )
            for e in edges
        ]
        upsert_file(conn, filepath, "swift", "sha123", syms, eds)
    finally:
        filepath.unlink(missing_ok=True)
    return conn


def _parse_and_extract_swift(source: str) -> list[Edge]:
    import importlib

    parser_mod = importlib.import_module("seam.indexer.parser")
    with tempfile.NamedTemporaryFile(suffix=".swift", mode="w", delete=False) as f:
        f.write(source)
        fname = f.name
    path = Path(fname)
    try:
        root = parser_mod.parse_swift(path)
        assert root is not None, "swift parse returned None"
        return extract_edges(root, "swift", path)
    finally:
        os.unlink(fname)


# ── G1: bare method name resolves to its qualified def in impact/trace seeds ──


class TestG1BareMethodSeedResolution:
    """expand_impact_seeds resolves a bare method name to its qualified def(s)."""

    def test_bare_method_resolves_to_qualified_seed(self) -> None:
        """A method stored only as 'Class.method' is reachable by its bare name."""
        conn = _seed_db([_sym("TTS.speakText", "method")], [])
        seeds = expand_impact_seeds(conn, "speakText")
        conn.close()
        assert "TTS.speakText" in seeds, f"qualified def not in seeds: {seeds}"
        # The bare name is still present so bare-stored edges (pre-Tier-B) also match.
        assert "speakText" in seeds

    def test_bare_method_with_multiple_qualified_defs_includes_all(self) -> None:
        """Homonym methods on different classes all become seeds (merged blast radius)."""
        conn = _seed_db(
            [_sym("TTS.speakText", "method"), _sym("Player.speakText", "method")], []
        )
        seeds = expand_impact_seeds(conn, "speakText")
        conn.close()
        assert "TTS.speakText" in seeds
        assert "Player.speakText" in seeds

    def test_standalone_function_seeds_unchanged(self) -> None:
        """A bare top-level function (exact symbol exists) is byte-identical to before."""
        conn = _seed_db([_sym("orchestrate", "function")], [])
        seeds = expand_impact_seeds(conn, "orchestrate")
        conn.close()
        assert seeds == ["orchestrate"]

    def test_unknown_bare_name_seeds_unchanged(self) -> None:
        """A name with no exact symbol and no qualified def stays [name]."""
        conn = _seed_db([_sym("Foo.bar", "method")], [])
        seeds = expand_impact_seeds(conn, "nonexistent")
        conn.close()
        assert seeds == ["nonexistent"]

    def test_impact_on_bare_method_is_found_with_upstream(self) -> None:
        """seam impact <bareMethod> reports found=True and the qualified caller."""
        conn = _seed_db(
            [_sym("C.handle", "method"), _sym("C.parse", "method")],
            [_edge("C.handle", "C.parse")],  # Tier-B-style qualified edge target
        )
        result = impact(conn, "parse", direction="upstream")
        conn.close()
        assert result["found"] is True, "bare method should resolve to its qualified def"
        callers = [e["name"] for e in result["upstream"]["WILL_BREAK"]]
        assert "C.handle" in callers, f"expected C.handle upstream, got {callers}"


# ── G2: Swift `Self` (capital) normalizes to the enclosing class ──────────────


class TestG2SwiftSelfNormalization:
    """Swift `Self.method()` resolves to '<EnclosingClass>.method', not 'Self.method'."""

    SELF_CALL_SRC = """\
class Companion {
    func handle() {
        let r = Self.parse(text: "x")
    }
    static func parse(text: String) -> Int { return 0 }
}
"""

    def test_self_call_targets_enclosing_class(self) -> None:
        edges = _parse_and_extract_swift(self.SELF_CALL_SRC)
        parse_calls = [e for e in edges if e["kind"] == "call" and e["target"].endswith("parse")]
        assert parse_calls, f"no call edge to parse found: {[e['target'] for e in edges]}"
        targets = {e["target"] for e in parse_calls}
        assert "Companion.parse" in targets, f"Self not normalized: {targets}"
        assert "Self.parse" not in targets, f"bogus 'Self.parse' target emitted: {targets}"
