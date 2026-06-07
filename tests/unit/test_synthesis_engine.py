"""Unit tests for seam/analysis/synthesis.py — pure synthesis engine.

TDD: tests written BEFORE implementation (RED first).

Coverage:
  A2-POSITIVE:   interface-override channel emits B.method→C.method call edges
  A2-NEGATIVE:   base with no matching impl → no edge emitted
  A2-CAP:        fanout_cap bounds the number of edges per channel
  A2-MULTI-BASE: one class implementing multiple interfaces
  A2-NO-METHODS: interface with no methods → no edges
  A2-DIRECT:     only direct subtypes (no transitive walk)
  A2-CONF:       synthesized edges have confidence=INFERRED and channel='interface-override'
  SIG-STABLE:    synthesize_edges signature accepts file_sources (for future channels)
  NEVER-RAISE:   bad input (None, empty, garbled) → returns [] never raises
"""

from typing import Any

# ── Helpers ───────────────────────────────────────────────────────────────────


def _sym(name: str, kind: str = "method") -> dict[str, Any]:
    """Minimal symbol dict for tests."""
    return {"name": name, "kind": kind, "file": "/fake.py", "start_line": 1, "end_line": 5}


def _edge(src: str, tgt: str, kind: str, confidence: str = "EXTRACTED") -> dict[str, Any]:
    """Minimal edge dict for tests."""
    return {
        "source": src,
        "target": tgt,
        "kind": kind,
        "file": "/fake.py",
        "line": 1,
        "confidence": confidence,
    }


def _synth_edges(symbols: list[dict], edges: list[dict], fanout_cap: int = 40) -> list[dict]:
    """Call synthesize_edges with empty file_sources."""
    from seam.analysis.synthesis import synthesize_edges
    return synthesize_edges(symbols, edges, file_sources={}, fanout_cap=fanout_cap)


# ── A2-POSITIVE: basic interface-override channel ────────────────────────────


class TestInterfaceOverridePositive:
    """A2 channel: base.method → impl.method synthesized call edges."""

    def test_single_interface_single_impl(self) -> None:
        """Interface IFace.process + ConcreteA implements IFace → edge IFace.process→ConcreteA.process."""
        symbols = [
            _sym("IFace", "interface"),
            _sym("IFace.process", "method"),
            _sym("ConcreteA", "class"),
            _sym("ConcreteA.process", "method"),
        ]
        edges = [
            _edge("ConcreteA", "IFace", "implements"),
        ]
        result = _synth_edges(symbols, edges)
        synth = [e for e in result if e.get("synthesized_by") == "interface-override"]
        targets = {(e["source"], e["target"]) for e in synth}
        assert ("IFace.process", "ConcreteA.process") in targets, (
            f"Expected IFace.process→ConcreteA.process; got {targets}"
        )

    def test_single_base_class_single_subclass(self) -> None:
        """Base.render + Sub extends Base → edge Base.render→Sub.render."""
        symbols = [
            _sym("Base", "class"),
            _sym("Base.render", "method"),
            _sym("Sub", "class"),
            _sym("Sub.render", "method"),
        ]
        edges = [
            _edge("Sub", "Base", "extends"),
        ]
        result = _synth_edges(symbols, edges)
        synth = [e for e in result if e.get("synthesized_by") == "interface-override"]
        targets = {(e["source"], e["target"]) for e in synth}
        assert ("Base.render", "Sub.render") in targets, (
            f"Expected Base.render→Sub.render; got {targets}"
        )

    def test_interface_with_multiple_implementations(self) -> None:
        """IFace.handle + two implementations → two synthesized edges."""
        symbols = [
            _sym("IFace", "interface"),
            _sym("IFace.handle", "method"),
            _sym("ImplA", "class"),
            _sym("ImplA.handle", "method"),
            _sym("ImplB", "class"),
            _sym("ImplB.handle", "method"),
        ]
        edges = [
            _edge("ImplA", "IFace", "implements"),
            _edge("ImplB", "IFace", "implements"),
        ]
        result = _synth_edges(symbols, edges)
        synth = {
            (e["source"], e["target"])
            for e in result
            if e.get("synthesized_by") == "interface-override"
        }
        assert ("IFace.handle", "ImplA.handle") in synth
        assert ("IFace.handle", "ImplB.handle") in synth

    def test_synthesized_edge_has_correct_fields(self) -> None:
        """Every synthesized edge must have kind='call', confidence='INFERRED', synthesized_by set."""
        symbols = [
            _sym("Svc", "interface"),
            _sym("Svc.run", "method"),
            _sym("Worker", "class"),
            _sym("Worker.run", "method"),
        ]
        edges = [_edge("Worker", "Svc", "implements")]
        result = _synth_edges(symbols, edges)
        synth = [e for e in result if e.get("synthesized_by") == "interface-override"]
        assert synth, "Expected at least one synthesized edge"
        for e in synth:
            assert e["kind"] == "call", f"Expected kind='call'; got {e['kind']}"
            assert e["confidence"] == "INFERRED", f"Expected confidence=INFERRED; got {e['confidence']}"
            assert e["synthesized_by"] == "interface-override"

    def test_source_field_is_base_method(self) -> None:
        """The synthesized edge source must be the BASE method (e.g. IFace.process)."""
        symbols = [
            _sym("IFace", "interface"),
            _sym("IFace.process", "method"),
            _sym("ConcreteA", "class"),
            _sym("ConcreteA.process", "method"),
        ]
        edges = [_edge("ConcreteA", "IFace", "implements")]
        result = _synth_edges(symbols, edges)
        synth = [e for e in result if e.get("synthesized_by") == "interface-override"]
        sources = {e["source"] for e in synth}
        assert "IFace.process" in sources, f"Expected IFace.process in sources; got {sources}"

    def test_target_field_is_impl_method(self) -> None:
        """The synthesized edge target must be the IMPLEMENTATION method (e.g. ConcreteA.process)."""
        symbols = [
            _sym("IFace", "interface"),
            _sym("IFace.process", "method"),
            _sym("ConcreteA", "class"),
            _sym("ConcreteA.process", "method"),
        ]
        edges = [_edge("ConcreteA", "IFace", "implements")]
        result = _synth_edges(symbols, edges)
        synth = [e for e in result if e.get("synthesized_by") == "interface-override"]
        targets = {e["target"] for e in synth}
        assert "ConcreteA.process" in targets, f"Expected ConcreteA.process in targets; got {targets}"


# ── A2-NEGATIVE: cases that must produce no edge ────────────────────────────


class TestInterfaceOverrideNegative:
    """A2 channel must NOT emit edges in these negative cases."""

    def test_no_edge_when_impl_has_no_matching_method(self) -> None:
        """If IFace has .process but Concrete has no .process → no synthesized edge."""
        symbols = [
            _sym("IFace", "interface"),
            _sym("IFace.process", "method"),
            _sym("Concrete", "class"),
            _sym("Concrete.other", "method"),  # different method name
        ]
        edges = [_edge("Concrete", "IFace", "implements")]
        result = _synth_edges(symbols, edges)
        synth = [e for e in result if e.get("synthesized_by") == "interface-override"]
        assert not synth, f"Expected no edges; got {synth}"

    def test_no_edge_when_base_has_no_methods(self) -> None:
        """Interface with no methods → no synthesized edges."""
        symbols = [
            _sym("IFace", "interface"),
            # No methods on IFace
            _sym("Concrete", "class"),
            _sym("Concrete.process", "method"),
        ]
        edges = [_edge("Concrete", "IFace", "implements")]
        result = _synth_edges(symbols, edges)
        synth = [e for e in result if e.get("synthesized_by") == "interface-override"]
        assert not synth, f"Expected no edges (no base methods); got {synth}"

    def test_no_edge_without_inheritance_edge(self) -> None:
        """No extends/implements edge between A and B → no synthesized edge even if same method names."""
        symbols = [
            _sym("A", "class"),
            _sym("A.process", "method"),
            _sym("B", "class"),
            _sym("B.process", "method"),
        ]
        edges = [_edge("A", "B", "call")]  # a call, not extends/implements
        result = _synth_edges(symbols, edges)
        synth = [e for e in result if e.get("synthesized_by") == "interface-override"]
        assert not synth, f"Expected no edges (no inheritance); got {synth}"

    def test_no_transitive_base_walk(self) -> None:
        """Only direct subtypes: A extends B extends C → only B→C-method edges emitted, not A→C-method."""
        symbols = [
            _sym("C", "class"),
            _sym("C.process", "method"),
            _sym("B", "class"),
            _sym("B.process", "method"),
            _sym("A", "class"),
            _sym("A.process", "method"),
        ]
        edges = [
            _edge("B", "C", "extends"),  # B extends C directly
            _edge("A", "B", "extends"),  # A extends B (transitive grandchild)
        ]
        result = _synth_edges(symbols, edges)
        synth = {
            (e["source"], e["target"])
            for e in result
            if e.get("synthesized_by") == "interface-override"
        }
        # Direct: C.process→B.process (B extends C) and B.process→A.process (A extends B)
        # Both are valid DIRECT one-hop links.
        # NOT acceptable: C.process→A.process (transitive) — but two direct edges ARE fine.
        # What we check: the engine does NOT emit C.process→A.process directly.
        assert ("C.process", "A.process") not in synth, (
            f"Transitive edge C.process→A.process must not be emitted; got {synth}"
        )

    def test_empty_symbols_and_edges(self) -> None:
        """Empty inputs → empty output, no error."""
        result = _synth_edges([], [])
        assert result == []

    def test_no_edge_for_call_kind_edges(self) -> None:
        """Call edges must NOT trigger override synthesis."""
        symbols = [
            _sym("A", "class"),
            _sym("A.process", "method"),
            _sym("B", "class"),
            _sym("B.process", "method"),
        ]
        edges = [_edge("A", "B", "call")]
        result = _synth_edges(symbols, edges)
        synth = [e for e in result if e.get("synthesized_by") == "interface-override"]
        assert not synth


# ── A2-CAP: fanout_cap boundary ──────────────────────────────────────────────


class TestFanoutCap:
    """synthesize_edges respects fanout_cap to bound output."""

    def test_cap_limits_implementations(self) -> None:
        """When 10 classes implement an interface, cap=3 limits to ≤3 edges."""
        symbols = [
            _sym("ISvc", "interface"),
            _sym("ISvc.run", "method"),
        ]
        for i in range(10):
            symbols.append(_sym(f"Worker{i}", "class"))
            symbols.append(_sym(f"Worker{i}.run", "method"))

        edges = [_edge(f"Worker{i}", "ISvc", "implements") for i in range(10)]

        result = _synth_edges(symbols, edges, fanout_cap=3)
        synth = [e for e in result if e.get("synthesized_by") == "interface-override"]
        # fanout_cap=3 → at most 3 synthesized edges for ISvc.run
        assert len(synth) <= 3, (
            f"Expected ≤3 edges with fanout_cap=3; got {len(synth)}: {synth}"
        )

    def test_no_cap_zero_allows_unlimited(self) -> None:
        """fanout_cap=0 means unlimited (no cap applied)."""
        n = 10
        symbols = [
            _sym("ISvc", "interface"),
            _sym("ISvc.run", "method"),
        ]
        for i in range(n):
            symbols.append(_sym(f"Worker{i}", "class"))
            symbols.append(_sym(f"Worker{i}.run", "method"))

        edges = [_edge(f"Worker{i}", "ISvc", "implements") for i in range(n)]

        result = _synth_edges(symbols, edges, fanout_cap=0)
        synth = [e for e in result if e.get("synthesized_by") == "interface-override"]
        assert len(synth) == n, f"Expected {n} edges with fanout_cap=0; got {len(synth)}"

    def test_default_cap_40_does_not_limit_small_graphs(self) -> None:
        """With fanout_cap=40 and only 3 implementors, all 3 edges are emitted."""
        symbols = [
            _sym("ISvc", "interface"),
            _sym("ISvc.run", "method"),
            _sym("A", "class"),
            _sym("A.run", "method"),
            _sym("B", "class"),
            _sym("B.run", "method"),
            _sym("C", "class"),
            _sym("C.run", "method"),
        ]
        edges = [
            _edge("A", "ISvc", "implements"),
            _edge("B", "ISvc", "implements"),
            _edge("C", "ISvc", "implements"),
        ]
        result = _synth_edges(symbols, edges, fanout_cap=40)
        synth = {
            (e["source"], e["target"])
            for e in result
            if e.get("synthesized_by") == "interface-override"
        }
        assert ("ISvc.run", "A.run") in synth
        assert ("ISvc.run", "B.run") in synth
        assert ("ISvc.run", "C.run") in synth


# ── Multi-base case ───────────────────────────────────────────────────────────


class TestMultiBase:
    """A class implementing multiple interfaces gets edges from all bases."""

    def test_class_implements_two_interfaces(self) -> None:
        """Worker implements ISvc + ILogger → edges from both bases."""
        symbols = [
            _sym("ISvc", "interface"),
            _sym("ISvc.run", "method"),
            _sym("ILogger", "interface"),
            _sym("ILogger.log", "method"),
            _sym("Worker", "class"),
            _sym("Worker.run", "method"),
            _sym("Worker.log", "method"),
        ]
        edges = [
            _edge("Worker", "ISvc", "implements"),
            _edge("Worker", "ILogger", "implements"),
        ]
        result = _synth_edges(symbols, edges)
        synth = {
            (e["source"], e["target"])
            for e in result
            if e.get("synthesized_by") == "interface-override"
        }
        assert ("ISvc.run", "Worker.run") in synth
        assert ("ILogger.log", "Worker.log") in synth


# ── Signature stability ───────────────────────────────────────────────────────


class TestSignatureStability:
    """synthesize_edges accepts file_sources kwarg (for future channels)."""

    def test_accepts_file_sources_kwarg(self) -> None:
        """synthesize_edges with file_sources={} does not raise."""
        from seam.analysis.synthesis import synthesize_edges

        result = synthesize_edges([], [], file_sources={"file.py": "class A: pass"}, fanout_cap=40)
        assert isinstance(result, list)

    def test_returns_list_of_dicts(self) -> None:
        """Return type is always list[dict], not None."""
        from seam.analysis.synthesis import synthesize_edges

        result = synthesize_edges([], [], file_sources={}, fanout_cap=40)
        assert isinstance(result, list)


# ── Never raises ─────────────────────────────────────────────────────────────


class TestNeverRaises:
    """synthesize_edges never raises on any input."""

    def test_none_like_symbols_do_not_raise(self) -> None:
        """Passing dicts with missing keys → returns [] without raising."""
        from seam.analysis.synthesis import synthesize_edges

        # Dicts with unexpected/missing keys should not crash the engine.
        bad_syms = [{"name": "A"}, {"kind": "method"}, {}]
        try:
            result = synthesize_edges(bad_syms, [], file_sources={}, fanout_cap=40)
            assert isinstance(result, list)
        except Exception as exc:
            raise AssertionError(
                f"synthesize_edges raised on bad input: {type(exc).__name__}: {exc}"
            ) from exc

    def test_none_like_edges_do_not_raise(self) -> None:
        """Passing dicts with missing edge keys → returns [] without raising."""
        from seam.analysis.synthesis import synthesize_edges

        bad_edges = [{"source": "A"}, {"kind": "implements"}, {}]
        try:
            result = synthesize_edges([], bad_edges, file_sources={}, fanout_cap=40)
            assert isinstance(result, list)
        except Exception as exc:
            raise AssertionError(
                f"synthesize_edges raised on bad edge input: {type(exc).__name__}: {exc}"
            ) from exc
