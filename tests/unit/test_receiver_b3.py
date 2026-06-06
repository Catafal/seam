"""Unit tests for Tier B slice B3: TS/JS member-expression call edges.

REGRESSION TEST: Before B2/B3, the TS/JS extractor only emitted call edges for
bare-identifier callees (func_child.type == "identifier"). Every obj.method() call
with a member_expression callee was SILENTLY DROPPED — producing zero edge for it.

B2 added the member_expression path; B3 formalises the regression guard.

Acceptance criteria:
  1. TS/JS extractor emits a call edge for member-expression callees (obj.method())
  2. target_name = rightmost identifier; receiver captured; bare-identifier calls byte-stable
  3. The new edges round-trip through DB and are queryable
  4. Regression test that would FAIL on the pre-B2 code (bare-only extractor)
  5. Negative cases: chained, optional-chain, and unknown shapes degrade gracefully
"""

import os
import tempfile
from pathlib import Path

import pytest

from seam.indexer.graph import Edge, extract_edges

# ── Helpers ───────────────────────────────────────────────────────────────────


def _parse_ts(source: str) -> list[Edge]:
    """Parse TypeScript source, extract all edges, return them."""
    import importlib

    parser_mod = importlib.import_module("seam.indexer.parser")
    parse_fn = getattr(parser_mod, "parse_typescript")
    with tempfile.NamedTemporaryFile(suffix=".ts", mode="w", delete=False) as f:
        f.write(source)
        fname = f.name
    path = Path(fname)
    try:
        root = parse_fn(path)
        assert root is not None, f"parse_typescript returned None for: {source!r}"
        return extract_edges(root, "typescript", path)
    finally:
        os.unlink(fname)


def _parse_js(source: str) -> list[Edge]:
    """Parse JavaScript source (uses TS parser), extract all edges."""
    import importlib

    parser_mod = importlib.import_module("seam.indexer.parser")
    parse_fn = getattr(parser_mod, "parse_typescript")
    with tempfile.NamedTemporaryFile(suffix=".js", mode="w", delete=False) as f:
        f.write(source)
        fname = f.name
    path = Path(fname)
    try:
        root = parse_fn(path)
        assert root is not None, f"parse_typescript returned None for: {source!r}"
        return extract_edges(root, "javascript", path)
    finally:
        os.unlink(fname)


def _call_edges(edges: list[Edge]) -> list[Edge]:
    """Return only call-kind edges."""
    return [e for e in edges if e["kind"] == "call"]


def _edge_by_target(edges: list[Edge], target: str) -> Edge | None:
    """Return first call edge with given target, or None."""
    return next((e for e in _call_edges(edges) if e["target"] == target), None)


# ── B3-R: Regression tests — prove obj.method() was dropped, is now emitted ──


class TestTsJsMemberEdgeRegression:
    """Regression guard: member-expression calls are no longer silently dropped.

    HISTORICAL BUG: The TS/JS extractor had:
        if func_child and func_child.type == "identifier":  # ONLY bare calls
            ...emit edge...
    Every obj.method() (member_expression callee) fell through and produced NO edge.

    This class documents the fix and provides a permanent regression guard.
    """

    # MINIMAL fixture: the simplest possible obj.method() call that was silently dropped.
    MINIMAL_MEMBER_CALL = """\
class Printer {
    print(): void {}
}
function test(p: Printer) {
    p.print();
}
"""

    def test_member_call_edge_emitted(self) -> None:
        """REGRESSION: p.print() must produce a 'print' call edge (was silently dropped)."""
        edges = _parse_ts(self.MINIMAL_MEMBER_CALL)
        call_targets = {e["target"] for e in _call_edges(edges)}
        assert "print" in call_targets, (
            f"REGRESSION: member-expression call 'p.print()' must produce an edge. "
            f"Got call targets: {call_targets}. "
            "Pre-B2/B3 code silently dropped every obj.method() call."
        )

    def test_member_call_has_correct_target(self) -> None:
        """target_name must be the rightmost identifier (the method name)."""
        edges = _parse_ts(self.MINIMAL_MEMBER_CALL)
        e = _edge_by_target(edges, "print")
        assert e is not None, "Expected 'print' call edge"
        assert e["target"] == "print", f"Expected target='print', got {e['target']!r}"

    def test_member_call_has_receiver(self) -> None:
        """Receiver text is captured (p.print() -> receiver='p')."""
        edges = _parse_ts(self.MINIMAL_MEMBER_CALL)
        e = _edge_by_target(edges, "print")
        assert e is not None, "Expected 'print' call edge"
        assert e.get("receiver") == "p", f"Expected receiver='p', got {e.get('receiver')!r}"

    def test_member_call_has_source(self) -> None:
        """Source is the enclosing function (test -> p.print())."""
        edges = _parse_ts(self.MINIMAL_MEMBER_CALL)
        e = _edge_by_target(edges, "print")
        assert e is not None, "Expected 'print' call edge"
        # Source should be 'test' (the enclosing function name)
        assert e["source"] == "test", f"Expected source='test', got {e['source']!r}"


# ── B3-JS: Same regression guard for JavaScript ────────────────────────────────


class TestJsMemberEdgeRegression:
    """JS: same regression guard — obj.execute() must produce a call edge."""

    MEMBER_CALL_SRC = """\
function doWork(obj) {
    obj.execute();
}
"""

    def test_js_member_call_edge_emitted(self) -> None:
        """REGRESSION: obj.execute() must produce an edge in JS (was dropped pre-B2)."""
        edges = _parse_js(self.MEMBER_CALL_SRC)
        call_targets = {e["target"] for e in _call_edges(edges)}
        assert "execute" in call_targets, (
            f"REGRESSION: JS member-expression call 'obj.execute()' must produce an edge. "
            f"Got: {call_targets}"
        )

    def test_js_member_call_receiver_captured(self) -> None:
        """JS: receiver text captured for obj.execute()."""
        edges = _parse_js(self.MEMBER_CALL_SRC)
        e = _edge_by_target(edges, "execute")
        assert e is not None
        assert e.get("receiver") == "obj", f"Expected receiver='obj', got {e.get('receiver')!r}"


# ── B3-S: Byte-stability of bare-identifier calls ────────────────────────────


class TestBareCallByteStability:
    """Bare-identifier calls must be unchanged (byte-stable) after the B2/B3 change."""

    BARE_CALL_SRC = """\
function callee(): void {}
function caller(): void {
    callee();
}
"""

    def test_bare_call_still_emitted(self) -> None:
        """callee() bare call must still produce an edge (byte-stable)."""
        edges = _parse_ts(self.BARE_CALL_SRC)
        e = _edge_by_target(edges, "callee")
        assert e is not None, "Bare call 'callee()' must still produce an edge"

    def test_bare_call_receiver_still_none(self) -> None:
        """Bare call's receiver must still be None (unchanged)."""
        edges = _parse_ts(self.BARE_CALL_SRC)
        e = _edge_by_target(edges, "callee")
        assert e is not None
        assert e.get("receiver") is None, (
            f"Bare call receiver must remain None, got {e.get('receiver')!r}"
        )

    def test_bare_call_kind_is_call(self) -> None:
        """Bare call edge kind must be 'call'."""
        edges = _parse_ts(self.BARE_CALL_SRC)
        e = _edge_by_target(edges, "callee")
        assert e is not None
        assert e["kind"] == "call"


# ── B3-N: Negative / graceful-degradation tests ──────────────────────────────


class TestMemberEdgeNegative:
    """Graceful degradation: awkward receiver shapes do not raise and degrade cleanly."""

    # this.method() — self-style receiver, common TS pattern
    THIS_CALL_SRC = """\
class Worker {
    process(): void {}
    run(): void {
        this.process();
    }
}
"""

    # Chained call: a.b.c() — receiver side is itself a member_expression
    CHAINED_CALL_SRC = """\
function test(a: any) {
    a.b.c();
}
"""

    # Optional chain: a?.method() — optional chaining operator
    OPTIONAL_CHAIN_SRC = """\
function test(a: any) {
    a?.method();
}
"""

    # Multiple member calls in same function
    MULTIPLE_MEMBER_CALLS_SRC = """\
class Logger {
    log(msg: string): void {}
    warn(msg: string): void {}
}
function report(logger: Logger) {
    logger.log("hello");
    logger.warn("danger");
}
"""

    def test_this_call_emitted_with_this_receiver(self) -> None:
        """this.process() must produce an edge with receiver='this'."""
        edges = _parse_ts(self.THIS_CALL_SRC)
        e = _edge_by_target(edges, "process")
        assert e is not None, "this.process() must produce a 'process' call edge"
        assert e.get("receiver") == "this", (
            f"Expected receiver='this' for this.process(), got {e.get('receiver')!r}"
        )

    def test_chained_call_does_not_raise(self) -> None:
        """a.b.c() must not raise; must produce a 'c' call edge."""
        try:
            edges = _parse_ts(self.CHAINED_CALL_SRC)
        except Exception as exc:
            pytest.fail(f"Chained call a.b.c() raised an exception: {exc}")
        # Must have a 'c' edge — receiver is 'a.b' (the LHS member expression text)
        e = _edge_by_target(edges, "c")
        assert e is not None, (
            f"a.b.c() must produce a 'c' call edge; got targets: "
            f"{[x['target'] for x in _call_edges(edges)]}"
        )

    def test_optional_chain_does_not_raise(self) -> None:
        """a?.method() must not raise — any graceful behavior is acceptable."""
        try:
            _parse_ts(self.OPTIONAL_CHAIN_SRC)
        except Exception as exc:
            pytest.fail(f"Optional chain a?.method() raised an exception: {exc}")

    def test_multiple_member_calls_all_emitted(self) -> None:
        """Multiple obj.method() calls in same function all produce separate edges."""
        edges = _parse_ts(self.MULTIPLE_MEMBER_CALLS_SRC)
        call_targets = {e["target"] for e in _call_edges(edges)}
        assert "log" in call_targets, f"logger.log() must produce a 'log' edge; got {call_targets}"
        assert "warn" in call_targets, (
            f"logger.warn() must produce a 'warn' edge; got {call_targets}"
        )

    def test_import_edges_unaffected(self) -> None:
        """Import edges remain unaffected (receiver=None) by the member-call change."""
        src = "import { foo } from './bar';\n"
        edges = _parse_ts(src)
        import_edges = [e for e in edges if e["kind"] == "import"]
        assert import_edges, "Import edge must still be emitted"
        for e in import_edges:
            assert e.get("receiver") is None, f"Import edge must have receiver=None; got {e!r}"
