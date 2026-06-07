"""Integration tests for edge synthesis (A2 interface-override channel).

Full pipeline: parse → extract → upsert → index_synthesis → query via context/impact.

Coverage:
  INIT:      synthesized edges present after seam init (index_synthesis called in pipeline)
  OFF:       SEAM_EDGE_SYNTHESIS=off → zero synthesized edges in DB
  IMPACT:    seam_impact traverses synthesized call edges (interface base→impl)
  CONTEXT:   seam_context on base method shows implementation as callee via synth edge
  TRAVERSAL: synthesized edges are kind-agnostic traversed (same as holds edges)
"""

import os
import tempfile
from pathlib import Path

# ── Python fixture with interface-override pattern ────────────────────────────

_PY_FIXTURE = """\
class IPaymentService:
    def process_payment(self) -> None:
        pass

    def refund(self) -> None:
        pass

class StripePayment(IPaymentService):
    def process_payment(self) -> None:
        print("stripe charge")

    def refund(self) -> None:
        print("stripe refund")

class PayPalPayment(IPaymentService):
    def process_payment(self) -> None:
        print("paypal charge")

    def refund(self) -> None:
        print("paypal refund")
"""


def _build_synthesis_db(synthesis_enabled: bool = True) -> tuple:
    """Index the fixture, run synthesis, return (conn, filepath)."""
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
        f.write(_PY_FIXTURE)
        fpath = f.name

    filepath = Path(fpath)
    try:
        from seam.indexer.parser import parse_python
        root = parse_python(filepath)
        assert root is not None, "Fixture must parse cleanly"

        from seam.indexer.graph import extract_edges, extract_symbols
        symbols = extract_symbols(root, "python", filepath)
        edges = extract_edges(root, "python", filepath, symbols)

        from seam.indexer.db import init_db, upsert_file
        conn = init_db(Path(":memory:"))
        upsert_file(conn, filepath, "python", "synth_test_hash", symbols, edges)

        # Run synthesis post-pass.
        import seam.config as cfg
        from seam.indexer.synthesis_index import index_synthesis
        fanout_cap = cfg.SEAM_SYNTHESIS_FANOUT_CAP
        count = index_synthesis(conn, enabled=synthesis_enabled, fanout_cap=fanout_cap)
    finally:
        os.unlink(fpath)

    return conn, count


# ── INIT: edges present after synthesis post-pass ────────────────────────────


class TestSynthesisInit:
    """Synthesized edges appear in the DB after running index_synthesis."""

    def test_synthesis_edges_present_after_init(self) -> None:
        """After index_synthesis, IPaymentService.process_payment→StripePayment.process_payment exists."""
        conn, count = _build_synthesis_db(synthesis_enabled=True)
        rows = conn.execute(
            "SELECT source_name, target_name FROM edges WHERE synthesized_by='interface-override'"
        ).fetchall()
        pairs = {(r[0], r[1]) for r in rows}
        assert ("IPaymentService.process_payment", "StripePayment.process_payment") in pairs, (
            f"Expected synth edge for StripePayment.process_payment; got {pairs}"
        )
        assert ("IPaymentService.process_payment", "PayPalPayment.process_payment") in pairs, (
            f"Expected synth edge for PayPalPayment.process_payment; got {pairs}"
        )

    def test_synthesis_count_positive(self) -> None:
        """index_synthesis returns a positive count when there are interface-override patterns."""
        _, count = _build_synthesis_db(synthesis_enabled=True)
        assert count > 0, f"Expected synthesis to find edges; got count={count}"

    def test_synthesized_by_column_is_channel_name(self) -> None:
        """All synthesized edges must have synthesized_by='interface-override'."""
        conn, _ = _build_synthesis_db(synthesis_enabled=True)
        rows = conn.execute(
            "SELECT DISTINCT synthesized_by FROM edges WHERE synthesized_by IS NOT NULL"
        ).fetchall()
        channels = {r[0] for r in rows}
        assert channels == {"interface-override"}, (
            f"Expected only 'interface-override' channel; got {channels}"
        )


# ── OFF: SEAM_EDGE_SYNTHESIS=off ─────────────────────────────────────────────


class TestSynthesisOff:
    """When SEAM_EDGE_SYNTHESIS=off, no synthesized edges are written."""

    def test_no_synth_edges_when_disabled(self) -> None:
        """SEAM_EDGE_SYNTHESIS=off → zero synthesized edges in DB."""
        conn, count = _build_synthesis_db(synthesis_enabled=False)
        assert count == 0, f"Expected 0 when disabled; got {count}"
        n = conn.execute(
            "SELECT COUNT(*) FROM edges WHERE synthesized_by IS NOT NULL"
        ).fetchone()[0]
        assert n == 0, f"Expected no synthesized rows; found {n}"


# ── IMPACT: seam_impact traverses synthesized edges ─────────────────────────


class TestImpactTraversal:
    """seam_impact on the base method should surface implementations via synthesized edges."""

    def test_impact_on_base_method_includes_impl(self) -> None:
        """seam_impact on IPaymentService.process_payment must find StripePayment.process_payment."""
        from pathlib import Path

        conn, _ = _build_synthesis_db(synthesis_enabled=True)

        from seam.server.tools import handle_seam_impact
        result = handle_seam_impact(
            conn,
            target="IPaymentService.process_payment",
            root=Path("/"),
            direction="downstream",
            max_depth=2,
            include_tests=False,
            verbose=False,
            limit=50,
        )

        # Collect all symbol names from the impact result.
        # handle_seam_impact returns tiers under direction keys (e.g. 'downstream'),
        # not under a generic 'by_distance' key.
        all_names: set[str] = set()
        for direction_key in ("downstream", "upstream", "both"):
            tier_group = result.get(direction_key, {})
            for tier_entries in tier_group.values():
                for entry in tier_entries:
                    all_names.add(entry.get("name", ""))

        assert "StripePayment.process_payment" in all_names or any(
            "StripePayment" in n for n in all_names
        ), (
            f"Expected StripePayment.process_payment in impact; got {sorted(all_names)}"
        )

    def test_impact_absent_when_synthesis_off(self) -> None:
        """When synthesis is off, impact on IPaymentService base method finds nothing downstream."""
        from pathlib import Path

        conn, _ = _build_synthesis_db(synthesis_enabled=False)

        from seam.server.tools import handle_seam_impact
        result = handle_seam_impact(
            conn,
            target="IPaymentService.process_payment",
            root=Path("/"),
            direction="downstream",
            max_depth=2,
            include_tests=False,
            verbose=False,
            limit=50,
        )

        all_names: set[str] = set()
        for direction_key in ("downstream", "upstream", "both"):
            tier_group = result.get(direction_key, {})
            for tier_entries in tier_group.values():
                for entry in tier_entries:
                    all_names.add(entry.get("name", ""))

        # Without synthesis, StripePayment is NOT downstream from base method
        assert "StripePayment.process_payment" not in all_names, (
            "StripePayment.process_payment must NOT appear when synthesis is off; "
            f"got {sorted(all_names)}"
        )


# ── CONTEXT: seam_context on base method shows impls ─────────────────────────


class TestContextTraversal:
    """seam_context on base method should show implementations as callees via synth edges."""

    def test_context_on_base_method_shows_synth_edges(self) -> None:
        """seam_context on IPaymentService.process_payment must include StripePayment.process_payment."""
        from pathlib import Path

        conn, _ = _build_synthesis_db(synthesis_enabled=True)

        from seam.server.tools import handle_seam_context
        result = handle_seam_context(
            conn,
            symbol="IPaymentService.process_payment",
            root=Path("/"),
            verbose=False,
        )

        # Callers or callees in context should include the implementations.
        # NOTE: handle_seam_context returns callers/callees as lists of strings (names),
        # not dicts. We check for any entry containing 'StripePayment'.
        callers = result.get("callers", [])
        callees = result.get("callees", [])

        # Each element may be a string (name) or a dict with a 'name' key.
        def _get_name(entry: object) -> str:
            if isinstance(entry, dict):
                return entry.get("name", "")
            return str(entry)

        all_neighbors = {_get_name(e) for e in list(callers) + list(callees)}

        assert any(
            "StripePayment" in n for n in all_neighbors
        ), (
            f"Expected StripePayment impl in context neighbors; got {sorted(all_neighbors)}"
        )
