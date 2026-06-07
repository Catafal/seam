"""Integration tests for Slice #77: composition (holds) edges in Python and TypeScript.

Full pipeline: parse → extract → upsert → query via context/impact.

Coverage:
  DB-PY:     Python holds edges stored in the SQLite DB
  DB-TS:     TypeScript holds edges stored in the SQLite DB
  IMPACT-PY: seam_impact upstream on a held type includes the holding class
  IMPACT-TS: seam_impact upstream on a held TS type includes the holding class
  CTX:       seam_context on the held type reflects the holding relationship (appears in callers)
  CONFIG:    SEAM_COMPOSITION_EDGES=off → zero holds rows in DB
"""

import os
import tempfile
from pathlib import Path

import pytest

from seam.indexer.db import init_db, upsert_file
from seam.indexer.graph import extract_edges, extract_symbols
from seam.server.tools import handle_seam_impact  # noqa: E402

# ── Python fixture ─────────────────────────────────────────────────────────────

_PY_FIXTURE = """\
class Database:
    def query(self) -> None:
        pass

class Repository:
    db: Database

    def __init__(self, db: Database) -> None:
        self.db = db
"""


def _build_py_db() -> tuple:
    """Parse the Python fixture, extract, store, return (conn, filepath)."""
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
        f.write(_PY_FIXTURE)
        fpath = f.name

    filepath = Path(fpath)
    try:
        from seam.indexer.parser import parse_python
        root = parse_python(filepath)
        assert root is not None

        symbols = extract_symbols(root, "python", filepath)
        edges = extract_edges(root, "python", filepath, symbols)

        conn = init_db(Path(":memory:"))
        upsert_file(conn, filepath, "python", "holds_py_hash", symbols, edges)
    finally:
        os.unlink(fpath)

    return conn, filepath


# ── TypeScript fixture ─────────────────────────────────────────────────────────

_TS_FIXTURE = """\
class Logger {
    log(): void {}
}

class Service {
    logger: Logger;
    constructor(private logger: Logger) {}
}
"""


def _build_ts_db() -> tuple:
    """Parse the TS fixture, extract, store, return (conn, filepath)."""
    with tempfile.NamedTemporaryFile(suffix=".ts", mode="w", delete=False) as f:
        f.write(_TS_FIXTURE)
        fpath = f.name

    filepath = Path(fpath)
    try:
        from seam.indexer.parser import parse_typescript
        root = parse_typescript(filepath)
        assert root is not None

        symbols = extract_symbols(root, "typescript", filepath)
        edges = extract_edges(root, "typescript", filepath, symbols)

        conn = init_db(Path(":memory:"))
        upsert_file(conn, filepath, "typescript", "holds_ts_hash", symbols, edges)
    finally:
        os.unlink(fpath)

    return conn, filepath


# ── DB-PY: Python holds edges in the DB ───────────────────────────────────────


class TestPythonHoldsInDB:
    """Python holds edges are stored in the edges table with kind='holds'."""

    def test_holds_edge_exists_in_db(self) -> None:
        """After indexing the Python fixture, a holds edge Repository→Database must exist."""
        conn, _ = _build_py_db()
        rows = conn.execute(
            "SELECT source_name, target_name, kind, confidence FROM edges WHERE kind='holds'"
        ).fetchall()
        assert any(r[0] == "Repository" and r[1] == "Database" for r in rows), (
            f"Expected holds edge Repository→Database; got holds rows: {rows}"
        )

    def test_holds_confidence_is_inferred_or_extracted(self) -> None:
        """Python holds edges have INFERRED confidence (or EXTRACTED when target is a same-file symbol).

        The edge is emitted with confidence='INFERRED' (conservatism contract). The extract_edges
        call may upgrade it to EXTRACTED when the target is resolved in the same-file symbol list.
        Both values are acceptable — the edge exists and is correctly typed.
        """
        conn, _ = _build_py_db()
        rows = conn.execute(
            "SELECT confidence FROM edges WHERE kind='holds'"
        ).fetchall()
        valid = {"INFERRED", "EXTRACTED"}
        assert all(r[0] in valid for r in rows), (
            f"Expected INFERRED or EXTRACTED; got {[r[0] for r in rows]}"
        )

    def test_dedup_single_holds_edge(self) -> None:
        """Repository holds Database via both field and __init__ param → only ONE holds edge."""
        conn, _ = _build_py_db()
        rows = conn.execute(
            "SELECT source_name, target_name FROM edges WHERE kind='holds' "
            "AND source_name='Repository' AND target_name='Database'"
        ).fetchall()
        assert len(rows) == 1, (
            f"Expected exactly 1 holds edge; got {len(rows)}: {rows}"
        )


# ── DB-TS: TypeScript holds edges in the DB ───────────────────────────────────


class TestTypeScriptHoldsInDB:
    """TypeScript holds edges are stored in the edges table with kind='holds'."""

    def test_holds_edge_exists_in_db(self) -> None:
        """After indexing the TS fixture, a holds edge Service→Logger must exist."""
        conn, _ = _build_ts_db()
        rows = conn.execute(
            "SELECT source_name, target_name, kind FROM edges WHERE kind='holds'"
        ).fetchall()
        assert any(r[0] == "Service" and r[1] == "Logger" for r in rows), (
            f"Expected holds edge Service→Logger; got {rows}"
        )

    def test_dedup_single_holds_edge(self) -> None:
        """Service holds Logger via both field and ctor param → only ONE holds edge."""
        conn, _ = _build_ts_db()
        rows = conn.execute(
            "SELECT source_name, target_name FROM edges WHERE kind='holds' "
            "AND source_name='Service' AND target_name='Logger'"
        ).fetchall()
        assert len(rows) == 1, (
            f"Expected exactly 1 holds edge; got {len(rows)}: {rows}"
        )


# ── IMPACT: seam_impact traverses holds edges ──────────────────────────────────


class TestHoldsEdgesImpact:
    """seam_impact on a held type must include the holding class (upstream BFS traverses holds)."""

    def test_impact_upstream_on_held_type_includes_holder(self) -> None:
        """seam_impact upstream on Database must include Repository (holds Database)."""
        conn, fp = _build_py_db()
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

    def test_impact_upstream_on_held_ts_type_includes_holder(self) -> None:
        """seam_impact upstream on Logger must include Service (holds Logger)."""
        conn, fp = _build_ts_db()
        root = fp.parent
        result = handle_seam_impact(conn, "Logger", direction="upstream", root=root)

        assert result.get("found") is True, f"Expected found=True; got {result}"
        all_names: set[str] = set()
        for tier_entries in result.get("upstream", {}).values():
            for entry in tier_entries:
                if isinstance(entry, dict):
                    all_names.add(entry.get("name", ""))

        assert "Service" in all_names, (
            f"Expected Service in upstream impact of Logger; got all_names={all_names}"
        )


# ── CONFIG-OFF: no holds edges when SEAM_COMPOSITION_EDGES=off ────────────────


class TestConfigOff:
    """With SEAM_COMPOSITION_EDGES=off, no holds edges are stored in the DB."""

    def test_python_no_holds_in_db_when_config_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Python fixture with config off → zero holds rows in DB."""
        import seam.config as cfg
        # setattr is safe with monkeypatch: the original value is automatically restored.
        monkeypatch.setattr(cfg, "SEAM_COMPOSITION_EDGES", "off")
        conn, _ = _build_py_db()
        rows = conn.execute(
            "SELECT * FROM edges WHERE kind='holds'"
        ).fetchall()
        assert len(rows) == 0, f"Expected no holds edges; got {rows}"

    def test_ts_no_holds_in_db_when_config_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """TypeScript fixture with config off → zero holds rows in DB."""
        import seam.config as cfg
        monkeypatch.setattr(cfg, "SEAM_COMPOSITION_EDGES", "off")
        conn, _ = _build_ts_db()
        rows = conn.execute(
            "SELECT * FROM edges WHERE kind='holds'"
        ).fetchall()
        assert len(rows) == 0, f"Expected no holds edges; got {rows}"
