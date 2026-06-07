"""Integration tests for Slice #78: composition (holds) edges in Go and Rust.

Full pipeline: parse → extract → upsert → query via impact.

Coverage:
  DB-GO:      Go holds edges stored in the SQLite DB
  DB-RUST:    Rust holds edges stored in the SQLite DB
  IMPACT-GO:  seam_impact upstream on a held Go type includes the holding struct
  IMPACT-RUST: seam_impact upstream on a held Rust type includes the holding struct
  CONFIG-GO:  SEAM_COMPOSITION_EDGES=off → zero holds rows in DB (Go)
  CONFIG-RUST: SEAM_COMPOSITION_EDGES=off → zero holds rows in DB (Rust)
"""

import os
import tempfile
from pathlib import Path

import pytest

from seam.indexer.db import init_db, upsert_file
from seam.indexer.graph import extract_edges, extract_symbols
from seam.server.tools import handle_seam_impact

# ── Go fixture ─────────────────────────────────────────────────────────────────

_GO_FIXTURE = """\
package main

// Database is a data access layer.
type Database struct {
    Path string
}

// Query runs a query.
func (d *Database) Query() {}

// Repository owns a Database.
type Repository struct {
    DB Database
}
"""


def _build_go_db() -> tuple:
    """Parse the Go fixture, extract, store, return (conn, filepath)."""
    with tempfile.NamedTemporaryFile(suffix=".go", mode="w", delete=False) as f:
        f.write(_GO_FIXTURE)
        fpath = f.name

    filepath = Path(fpath)
    try:
        from seam.indexer.parser import parse_go
        root = parse_go(filepath)
        assert root is not None

        symbols = extract_symbols(root, "go", filepath)
        edges = extract_edges(root, "go", filepath, symbols)

        conn = init_db(Path(":memory:"))
        upsert_file(conn, filepath, "go", "holds_go_hash", symbols, edges)
    finally:
        os.unlink(fpath)

    return conn, filepath


# ── Rust fixture ───────────────────────────────────────────────────────────────

_RUST_FIXTURE = """\
/// A simple cache.
struct Cache {
    capacity: usize,
}

impl Cache {
    fn get(&self) {}
}

/// A service that uses the cache.
struct Service {
    cache: Cache,
}
"""


def _build_rust_db() -> tuple:
    """Parse the Rust fixture, extract, store, return (conn, filepath)."""
    with tempfile.NamedTemporaryFile(suffix=".rs", mode="w", delete=False) as f:
        f.write(_RUST_FIXTURE)
        fpath = f.name

    filepath = Path(fpath)
    try:
        from seam.indexer.parser import parse_rust
        root = parse_rust(filepath)
        assert root is not None

        symbols = extract_symbols(root, "rust", filepath)
        edges = extract_edges(root, "rust", filepath, symbols)

        conn = init_db(Path(":memory:"))
        upsert_file(conn, filepath, "rust", "holds_rust_hash", symbols, edges)
    finally:
        os.unlink(fpath)

    return conn, filepath


# ── DB-GO: Go holds edges in the DB ───────────────────────────────────────────


class TestGoHoldsInDB:
    """Go holds edges are stored in the edges table with kind='holds'."""

    def test_holds_edge_exists_in_db(self) -> None:
        """After indexing the Go fixture, a holds edge Repository→Database must exist."""
        conn, _ = _build_go_db()
        rows = conn.execute(
            "SELECT source_name, target_name, kind, confidence FROM edges WHERE kind='holds'"
        ).fetchall()
        assert any(r[0] == "Repository" and r[1] == "Database" for r in rows), (
            f"Expected holds edge Repository→Database; got holds rows: {rows}"
        )

    def test_holds_confidence_is_inferred(self) -> None:
        """Go holds edges have INFERRED confidence (conservatism contract)."""
        conn, _ = _build_go_db()
        rows = conn.execute(
            "SELECT confidence FROM edges WHERE kind='holds'"
        ).fetchall()
        valid = {"INFERRED", "EXTRACTED"}
        assert all(r[0] in valid for r in rows), (
            f"Expected INFERRED or EXTRACTED; got {[r[0] for r in rows]}"
        )

    def test_no_duplicate_holds_edges(self) -> None:
        """One field of type Database → exactly one holds edge Repository→Database."""
        conn, _ = _build_go_db()
        rows = conn.execute(
            "SELECT source_name, target_name FROM edges WHERE kind='holds' "
            "AND source_name='Repository' AND target_name='Database'"
        ).fetchall()
        assert len(rows) == 1, (
            f"Expected exactly 1 holds edge; got {len(rows)}: {rows}"
        )


# ── DB-RUST: Rust holds edges in the DB ───────────────────────────────────────


class TestRustHoldsInDB:
    """Rust holds edges are stored in the edges table with kind='holds'."""

    def test_holds_edge_exists_in_db(self) -> None:
        """After indexing the Rust fixture, a holds edge Service→Cache must exist."""
        conn, _ = _build_rust_db()
        rows = conn.execute(
            "SELECT source_name, target_name, kind FROM edges WHERE kind='holds'"
        ).fetchall()
        assert any(r[0] == "Service" and r[1] == "Cache" for r in rows), (
            f"Expected holds edge Service→Cache; got holds rows: {rows}"
        )

    def test_holds_confidence_is_inferred(self) -> None:
        """Rust holds edges have INFERRED or EXTRACTED confidence."""
        conn, _ = _build_rust_db()
        rows = conn.execute(
            "SELECT confidence FROM edges WHERE kind='holds'"
        ).fetchall()
        valid = {"INFERRED", "EXTRACTED"}
        assert all(r[0] in valid for r in rows), (
            f"Expected INFERRED or EXTRACTED; got {[r[0] for r in rows]}"
        )

    def test_no_duplicate_holds_edges(self) -> None:
        """One field of type Cache → exactly one holds edge Service→Cache."""
        conn, _ = _build_rust_db()
        rows = conn.execute(
            "SELECT source_name, target_name FROM edges WHERE kind='holds' "
            "AND source_name='Service' AND target_name='Cache'"
        ).fetchall()
        assert len(rows) == 1, (
            f"Expected exactly 1 holds edge; got {len(rows)}: {rows}"
        )


# ── IMPACT-GO: seam_impact traverses Go holds edges ───────────────────────────


class TestGoHoldsImpact:
    """seam_impact on a held Go type includes the holding struct upstream."""

    def test_impact_upstream_on_held_type_includes_holder(self) -> None:
        """seam_impact upstream on Database must include Repository (holds Database)."""
        conn, fp = _build_go_db()
        root = fp.parent
        result = handle_seam_impact(conn, "Database", direction="upstream", root=root)

        assert result.get("found") is True, f"Expected found=True; got {result}"
        # Collect all symbol names in upstream tiers
        all_names: set[str] = set()
        for tier_entries in result.get("upstream", {}).values():
            for entry in tier_entries:
                if isinstance(entry, dict):
                    all_names.add(entry.get("name", ""))

        assert "Repository" in all_names, (
            f"Expected Repository in upstream impact of Database; got all_names={all_names}"
        )


# ── IMPACT-RUST: seam_impact traverses Rust holds edges ───────────────────────


class TestRustHoldsImpact:
    """seam_impact on a held Rust type includes the holding struct upstream."""

    def test_impact_upstream_on_held_type_includes_holder(self) -> None:
        """seam_impact upstream on Cache must include Service (holds Cache)."""
        conn, fp = _build_rust_db()
        root = fp.parent
        result = handle_seam_impact(conn, "Cache", direction="upstream", root=root)

        assert result.get("found") is True, f"Expected found=True; got {result}"
        all_names: set[str] = set()
        for tier_entries in result.get("upstream", {}).values():
            for entry in tier_entries:
                if isinstance(entry, dict):
                    all_names.add(entry.get("name", ""))

        assert "Service" in all_names, (
            f"Expected Service in upstream impact of Cache; got all_names={all_names}"
        )


# ── CONFIG: no holds edges when SEAM_COMPOSITION_EDGES=off ────────────────────


class TestConfigOff:
    """With SEAM_COMPOSITION_EDGES=off, no holds edges are stored in the DB."""

    def test_go_no_holds_in_db_when_config_off(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Go fixture with config off → zero holds rows in DB."""
        import seam.config as cfg

        monkeypatch.setattr(cfg, "SEAM_COMPOSITION_EDGES", "off")
        conn, _ = _build_go_db()
        rows = conn.execute(
            "SELECT * FROM edges WHERE kind='holds'"
        ).fetchall()
        assert len(rows) == 0, f"Expected no holds edges; got {rows}"

    def test_rust_no_holds_in_db_when_config_off(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Rust fixture with config off → zero holds rows in DB."""
        import seam.config as cfg

        monkeypatch.setattr(cfg, "SEAM_COMPOSITION_EDGES", "off")
        conn, _ = _build_rust_db()
        rows = conn.execute(
            "SELECT * FROM edges WHERE kind='holds'"
        ).fetchall()
        assert len(rows) == 0, f"Expected no holds edges; got {rows}"
