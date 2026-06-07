"""Integration tests for Slice #80: composition (holds) edges in Swift.

Full pipeline: parse → extract → upsert → query via impact.

Coverage:
  DB-SWIFT:      Swift holds edges stored in the SQLite DB
  DEDUP-DB:      field + init param of same type → one row in DB
  IMPACT-SWIFT:  seam_impact upstream on a held ObservableObject includes its observers
  CONFIG:        SEAM_COMPOSITION_EDGES=off → zero holds rows in DB
"""

import os
import tempfile
from pathlib import Path

import pytest

from seam.indexer.db import init_db, upsert_file
from seam.indexer.graph import extract_edges, extract_symbols
from seam.server.tools import handle_seam_impact

# ── Swift fixture (SwiftUI-style composition) ─────────────────────────────────
#
# UserStore is an ObservableObject held by ContentView (@ObservedObject).
# ContentView also receives a Router in its init parameter.
# This fixture exercises:
#   - @ObservedObject wrapper (SWIFT-WRAPPER acceptance)
#   - init param holds (SWIFT-INIT acceptance)
#   - dedup (ContentView holds UserStore via both property + init)
#   - seam_impact upstream: querying UserStore should include ContentView

_SWIFT_FIXTURE = """\
class UserStore {
    func fetchUsers() {}
}

class Router {
    func navigate() {}
}

class ContentView {
    @ObservedObject var store: UserStore
    var router: Router

    init(store: UserStore, router: Router) {
        self.store = store
        self.router = router
    }
}
"""


def _build_swift_db() -> tuple:
    """Parse the Swift fixture, extract, store, return (conn, filepath)."""
    with tempfile.NamedTemporaryFile(suffix=".swift", mode="w", delete=False) as f:
        f.write(_SWIFT_FIXTURE)
        fpath = f.name

    filepath = Path(fpath)
    try:
        from seam.indexer.parser import parse_swift
        root = parse_swift(filepath)
        assert root is not None, "Swift fixture failed to parse"

        symbols = extract_symbols(root, "swift", filepath)
        edges = extract_edges(root, "swift", filepath, symbols)

        conn = init_db(Path(":memory:"))
        upsert_file(conn, filepath, "swift", "holds_swift_hash", symbols, edges)
    finally:
        os.unlink(fpath)

    return conn, filepath


# ── DB-SWIFT: holds edges stored in the DB ───────────────────────────────────


class TestSwiftHoldsInDB:
    """Swift holds edges are stored in the edges table with kind='holds'."""

    def test_holds_edge_store_exists(self) -> None:
        """After indexing, ContentView→UserStore holds edge must exist in the DB."""
        conn, _ = _build_swift_db()
        rows = conn.execute(
            "SELECT source_name, target_name, kind, confidence FROM edges WHERE kind='holds'"
        ).fetchall()
        assert any(r[0] == "ContentView" and r[1] == "UserStore" for r in rows), (
            f"Expected holds edge ContentView→UserStore; got holds rows: {rows}"
        )

    def test_holds_edge_router_exists(self) -> None:
        """ContentView→Router holds edge must exist (plain property)."""
        conn, _ = _build_swift_db()
        rows = conn.execute(
            "SELECT source_name, target_name FROM edges WHERE kind='holds'"
        ).fetchall()
        assert any(r[0] == "ContentView" and r[1] == "Router" for r in rows), (
            f"Expected holds edge ContentView→Router; got rows: {rows}"
        )

    def test_holds_confidence_is_inferred_or_extracted(self) -> None:
        """Swift holds edges have INFERRED or EXTRACTED confidence."""
        conn, _ = _build_swift_db()
        rows = conn.execute(
            "SELECT confidence FROM edges WHERE kind='holds'"
        ).fetchall()
        valid = {"INFERRED", "EXTRACTED"}
        assert all(r[0] in valid for r in rows), (
            f"Expected INFERRED or EXTRACTED confidence; got {[r[0] for r in rows]}"
        )


# ── DEDUP-DB: deduplication check ────────────────────────────────────────────


class TestSwiftHoldsDedup:
    """ContentView holds UserStore via @ObservedObject property AND init param → only ONE row."""

    def test_dedup_single_holds_edge(self) -> None:
        """ContentView holds UserStore via both property and init param → one DB row."""
        conn, _ = _build_swift_db()
        rows = conn.execute(
            "SELECT source_name, target_name FROM edges "
            "WHERE kind='holds' AND source_name='ContentView' AND target_name='UserStore'"
        ).fetchall()
        assert len(rows) == 1, (
            f"Expected exactly 1 holds edge ContentView→UserStore; got {len(rows)}: {rows}"
        )


# ── IMPACT-SWIFT: seam_impact traverses holds edges ───────────────────────────


class TestSwiftHoldsImpact:
    """seam_impact upstream on a held type must list the holding Swift class."""

    def test_impact_upstream_on_userstore_includes_contentview(self) -> None:
        """seam_impact upstream on UserStore must include ContentView (holds UserStore)."""
        conn, fp = _build_swift_db()
        root = fp.parent
        result = handle_seam_impact(conn, "UserStore", direction="upstream", root=root)

        assert result.get("found") is True, f"Expected found=True; got {result}"

        all_names: set[str] = set()
        for tier_entries in result.get("upstream", {}).values():
            for entry in tier_entries:
                if isinstance(entry, dict):
                    all_names.add(entry.get("name", ""))

        assert "ContentView" in all_names, (
            f"Expected ContentView in upstream impact of UserStore; got all_names={all_names}"
        )

    def test_impact_upstream_on_router_includes_contentview(self) -> None:
        """seam_impact upstream on Router must include ContentView."""
        conn, fp = _build_swift_db()
        root = fp.parent
        result = handle_seam_impact(conn, "Router", direction="upstream", root=root)

        assert result.get("found") is True, f"Expected found=True; got {result}"

        all_names: set[str] = set()
        for tier_entries in result.get("upstream", {}).values():
            for entry in tier_entries:
                if isinstance(entry, dict):
                    all_names.add(entry.get("name", ""))

        assert "ContentView" in all_names, (
            f"Expected ContentView in upstream impact of Router; got all_names={all_names}"
        )


# ── CONFIG: no holds edges when SEAM_COMPOSITION_EDGES=off ───────────────────


class TestSwiftConfigOff:
    """With SEAM_COMPOSITION_EDGES=off, no holds edges are stored in the DB."""

    def test_no_holds_in_db_when_config_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Swift fixture with config off → zero holds rows in DB."""
        import seam.config as cfg
        monkeypatch.setattr(cfg, "SEAM_COMPOSITION_EDGES", "off")
        conn, _ = _build_swift_db()
        rows = conn.execute(
            "SELECT * FROM edges WHERE kind='holds'"
        ).fetchall()
        assert len(rows) == 0, f"Expected no holds edges; got {rows}"
