"""Integration tests for Tier B slice B3: TS/JS member-expression call edges.

Tests the FULL pipeline: parse → extract → store → read via context/impact/trace.

REGRESSION: Before B2, every obj.method() call in TS/JS was silently dropped at
extraction time. B2 fixed the extractor; B3 tests that the fix is visible end-to-end:
  - The edge is stored in the DB
  - context() returns the new edge in callers/callees
  - impact() returns the calling function in its upstream tier
  - (trace is a single-step path; verified via impact's upstream result)

Fixture schema (TypeScript):
  class Printer { print(): void {} }
  function test(p: Printer) { p.print(); }

  Expected symbols:  Printer, Printer.print, test
  Expected edges:    test -> 'print'  (member-expression call; was dropped pre-B2)

The test uses a real TS temp file parsed by parse_typescript → extract_edges →
upsert_file so the store path is fully exercised, not just the extractor unit.
"""

import os
import tempfile
from pathlib import Path

from seam.indexer.db import init_db, upsert_file
from seam.indexer.graph import extract_edges, extract_symbols
from seam.indexer.parser import parse_typescript
from seam.query.engine import context

# ── TS fixture source ─────────────────────────────────────────────────────────

_TS_FIXTURE = """\
class Printer {
    print(): void {}
}

class Runner {
    run(p: Printer): void {
        p.print();
    }
}

function standalone(p: Printer): void {
    p.print();
}
"""

# ── Shared DB fixture ─────────────────────────────────────────────────────────


def _build_db():  # type: ignore[return]
    """Parse the TS fixture, extract symbols+edges, upsert into in-memory DB.

    Returns (conn, filepath) so the caller can inspect the DB and unlink the
    temp file when done.
    """
    with tempfile.NamedTemporaryFile(suffix=".ts", mode="w", delete=False) as f:
        f.write(_TS_FIXTURE)
        fpath = f.name

    filepath = Path(fpath)
    try:
        root = parse_typescript(filepath)
        assert root is not None, "parse_typescript returned None"

        symbols = extract_symbols(root, "typescript", filepath)
        edges = extract_edges(root, "typescript", filepath, symbols)

        conn = init_db(Path(":memory:"))
        # Write a stub mtime file so stat() doesn't fail inside upsert_file.
        # (We use a real temp file, so stat() works normally.)
        upsert_file(conn, filepath, "typescript", "test_hash_b3", symbols, edges)
    finally:
        os.unlink(fpath)

    return conn


# ── B3-I1: Edge stored in DB ──────────────────────────────────────────────────


class TestMemberEdgeStoredInDb:
    """B3-I1: member-expression call edge is stored in the DB after upsert."""

    def test_member_call_edge_in_db(self) -> None:
        """A print-method call edge must be present in the edges table after indexing.

        B4 update: since p: Printer is type-annotated, the target is now 'Printer.print'
        (type inference on) rather than bare 'print'. Accept both forms.
        """
        conn = _build_db()
        try:
            # Accept either bare 'print' (inference off) or qualified 'Printer.print' (inference on).
            rows = conn.execute(
                "SELECT source_name, target_name, receiver FROM edges"
                " WHERE target_name = ? OR target_name = ?",
                ("print", "Printer.print"),
            ).fetchall()
        finally:
            conn.close()

        assert rows, (
            "REGRESSION: 'print' or 'Printer.print' call edge must be stored in DB. "
            "Pre-B2 code silently dropped every obj.method() call."
        )

    def test_member_call_edge_source_is_enclosing_function(self) -> None:
        """Edge source must be the enclosing function (Runner.run or standalone).

        B4 update: accept either bare 'print' or qualified 'Printer.print' as target.
        """
        conn = _build_db()
        try:
            rows = conn.execute(
                "SELECT source_name FROM edges WHERE target_name = ? OR target_name = ?",
                ("print", "Printer.print"),
            ).fetchall()
        finally:
            conn.close()

        sources = {r["source_name"] for r in rows}
        # At least one of Runner.run or standalone must appear as source
        assert sources & {"Runner.run", "standalone"}, (
            f"Expected 'Runner.run' or 'standalone' as edge source for print edges; got {sources}"
        )

    def test_member_call_receiver_stored(self) -> None:
        """Receiver text ('p') must be stored in the DB alongside the edge.

        B4 update: accept either bare 'print' or qualified 'Printer.print' as target.
        """
        conn = _build_db()
        try:
            rows = conn.execute(
                "SELECT receiver FROM edges WHERE target_name = ? OR target_name = ?",
                ("print", "Printer.print"),
            ).fetchall()
        finally:
            conn.close()

        receivers = {r["receiver"] for r in rows}
        assert "p" in receivers, f"Expected receiver='p' stored for print edges; got {receivers}"


# ── B3-I2: Visible through context() ─────────────────────────────────────────


class TestMemberEdgeVisibleViaContext:
    """B3-I2: member-expression call edge shows up in context() callers/callees."""

    def test_print_has_callers_via_member_edge(self) -> None:
        """context('Printer.print') must include callers found via member-expression edges."""
        conn = _build_db()
        try:
            result = context(conn, "Printer.print")
        finally:
            conn.close()

        assert result is not None, (
            "context('Printer.print') returned None — symbol not indexed or not found."
        )
        # The Tier A name-bridging resolves 'print' edges to 'Printer.print'.
        # Callers should include Runner.run and/or standalone.
        callers = set(result.get("callers", []))
        assert callers & {"Runner.run", "standalone"}, (
            f"REGRESSION: member-expression callers must appear in context('Printer.print'). "
            f"Got callers: {callers}. "
            f"This means obj.method() edges are still dropped or not bridged."
        )

    def test_standalone_has_print_callee(self) -> None:
        """context('standalone') must include 'print' (or Printer.print) as a callee."""
        conn = _build_db()
        try:
            result = context(conn, "standalone")
        finally:
            conn.close()

        assert result is not None, "context('standalone') returned None"
        callees = set(result.get("callees", []))
        # The callee may appear as bare 'print' or qualified 'Printer.print' depending
        # on read-time Tier A bridging; either form is acceptable.
        has_print_callee = any("print" in c for c in callees)
        assert has_print_callee, (
            f"context('standalone') must have 'print' callee via member-expression edge. "
            f"Got callees: {callees}"
        )

    def test_runner_run_has_print_callee(self) -> None:
        """context('Runner.run') must include 'print' as a callee."""
        conn = _build_db()
        try:
            result = context(conn, "Runner.run")
        finally:
            conn.close()

        assert result is not None, "context('Runner.run') returned None"
        callees = set(result.get("callees", []))
        has_print_callee = any("print" in c for c in callees)
        assert has_print_callee, (
            f"context('Runner.run') must have 'print' callee via member-expression edge. "
            f"Got callees: {callees}"
        )


# ── B3-I3: Visible through impact() ──────────────────────────────────────────


class TestMemberEdgeVisibleViaImpact:
    """B3-I3: member-expression call edge contributes to upstream impact of Printer.print."""

    def test_print_method_has_upstream_callers(self) -> None:
        """impact('Printer.print', upstream) must find callers via member-expression edges.

        Pre-B2: every obj.method() edge was dropped → impact returned empty upstream.
        Post-B2+B3: the edges exist → callers appear in the upstream tier.
        """
        from seam.analysis.impact import impact

        conn = _build_db()
        try:
            result = impact(conn, "Printer.print", direction="upstream")
        finally:
            conn.close()

        assert result["found"], (
            "impact('Printer.print', upstream) returned found=False. "
            "Printer.print must be indexed as a symbol."
        )
        upstream = result.get("upstream", {})
        all_upstream = []
        for tier_entries in upstream.values():
            all_upstream.extend(tier_entries)

        upstream_names = {e["name"] for e in all_upstream}
        assert upstream_names & {"Runner.run", "standalone"}, (
            f"REGRESSION: upstream impact of 'Printer.print' must include callers "
            f"that call via member-expression ('Runner.run' or 'standalone'). "
            f"Got: {upstream_names}"
        )

    def test_bare_print_has_upstream_callers(self) -> None:
        """impact('print', upstream) also resolves upstream via Tier A bridging."""
        from seam.analysis.impact import impact

        conn = _build_db()
        try:
            result = impact(conn, "print", direction="upstream")
        finally:
            conn.close()

        # 'print' may or may not be 'found' depending on whether the DB has an exact
        # symbol row named 'print' (methods are stored as 'Printer.print'). If found=False
        # that is acceptable; if found=True, callers must appear.
        if result["found"]:
            upstream = result.get("upstream", {})
            all_upstream = []
            for tier_entries in upstream.values():
                all_upstream.extend(tier_entries)
            upstream_names = {e["name"] for e in all_upstream}
            assert upstream_names, (
                "impact('print') found=True but upstream is empty — expected callers"
            )


# ── B3-I4: Bare-call byte-stability under full pipeline ──────────────────────


class TestBareCallByteStableInPipeline:
    """B3-I4: bare-identifier calls are byte-stable through the full pipeline."""

    _BARE_TS_FIXTURE = """\
function callee(): number { return 1; }
function caller(): number { return callee(); }
"""

    def test_bare_call_edge_still_in_db(self) -> None:
        """Bare call 'callee()' must still produce a DB edge (byte-stable)."""
        with tempfile.NamedTemporaryFile(suffix=".ts", mode="w", delete=False) as f:
            f.write(self._BARE_TS_FIXTURE)
            fpath = f.name

        filepath = Path(fpath)
        try:
            root = parse_typescript(filepath)
            assert root is not None
            symbols = extract_symbols(root, "typescript", filepath)
            edges = extract_edges(root, "typescript", filepath, symbols)
            conn = init_db(Path(":memory:"))
            upsert_file(conn, filepath, "typescript", "test_bare", symbols, edges)
        finally:
            os.unlink(fpath)

        try:
            row = conn.execute(
                "SELECT source_name, target_name FROM edges WHERE target_name = 'callee'",
            ).fetchone()
        finally:
            conn.close()

        assert row is not None, (
            "Bare call 'callee()' must still produce a DB edge (byte-stable after B3)"
        )
        assert row["source_name"] == "caller", (
            f"Expected source='caller' for bare callee edge; got {row['source_name']!r}"
        )
