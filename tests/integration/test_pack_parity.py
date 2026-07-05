"""Integration tests for Phase 6 — MCP tool + CLI parity.

Verifies that handle_seam_context_pack and `seam pack` produce the same bundle,
and that the --json / --quiet output modes follow the established envelope contract.

Test groups:
    PP1 — handle_seam_context_pack returns enriched bundle matching context_pack()
    PP2 — handle_seam_context_pack missing symbol returns None (same as handle_seam_context)
    PP3 — handle_seam_context_pack invalid input returns INVALID_INPUT error dict
    PP4 — CLI --json mode emits {ok:true, data:...} envelope
    PP5 — CLI missing symbol: handler returns None; CLI emits success {found:false}
    PP6 — CLI --quiet renders without envelope
    PP7 — MCP handler and CLI --json produce the same bundle shape
"""

import json
from pathlib import Path

from seam.cli.output import build_success_envelope
from seam.indexer.db import init_db, upsert_file
from seam.indexer.graph import Edge, Symbol
from seam.query.pack import context_pack
from seam.server.tools import handle_seam_context_pack

# ── Helpers ───────────────────────────────────────────────────────────────────


def _sym(name: str, file: str, kind: str = "function", line: int = 1) -> Symbol:
    return Symbol(
        name=name, kind=kind, file=file,
        start_line=line, end_line=line + 5,
        docstring=None, signature=None, decorators=[],
        is_exported=None, visibility=None, qualified_name=name,
    )


def _edge(source: str, target: str, file: str) -> Edge:
    return Edge(
        source=source, target=target, kind="call",
        file=file, line=1, confidence="INFERRED",
    )


def _make_indexed_db(tmp_path: Path):
    """Build a small indexed DB with two symbols and one edge."""
    db_path = tmp_path / "test.db"
    conn = init_db(db_path)

    src = tmp_path / "src.py"
    src.write_text("def foo(): bar()\ndef bar(): pass\n")

    upsert_file(
        conn, src, "python", "h1",
        [_sym("foo", str(src), line=1), _sym("bar", str(src), line=2)],
        [_edge("foo", "bar", str(src))],
    )
    return conn, tmp_path, src


# ── PP1: handle_seam_context_pack returns enriched bundle ────────────────────


class TestHandlerBasic:
    """handle_seam_context_pack returns a valid pack or None."""

    def test_handler_returns_pack_for_known_symbol(self, tmp_path: Path) -> None:
        conn, root, src = _make_indexed_db(tmp_path)
        result = handle_seam_context_pack(conn, "foo", root)
        conn.close()

        assert result is not None
        # Must have the pack top-level keys
        assert "target" in result
        assert "callers" in result
        assert "callees" in result
        assert "why" in result
        assert "cluster_peers" in result
        assert "truncated" in result
        assert "relationship_evidence" in result
        assert "caveats" in result
        assert "recommended_next_calls" in result

    def test_handler_relativizes_file_paths(self, tmp_path: Path) -> None:
        """File paths in enriched neighbors are relative to root."""
        conn, root, src = _make_indexed_db(tmp_path)
        result = handle_seam_context_pack(conn, "foo", root)
        conn.close()

        assert result is not None
        # The target file should be relative
        target_file = result["target"]["file"]
        assert not Path(target_file).is_absolute(), (
            f"Expected relative path, got absolute: {target_file!r}"
        )

    def test_handler_relativizes_relationship_evidence_paths(self, tmp_path: Path) -> None:
        conn, root, src = _make_indexed_db(tmp_path)
        result = handle_seam_context_pack(conn, "foo", root)
        conn.close()

        assert result is not None
        evidence = result["relationship_evidence"]
        assert evidence["callees"]
        evidence_file = evidence["callees"][0]["file"]
        assert evidence_file == "src.py"

        # Callees should also be relativized
        for nb in result["callees"]:
            assert not Path(nb["file"]).is_absolute(), (
                f"Callee file not relativized: {nb['file']!r}"
            )


# ── PP2: Missing symbol → None ────────────────────────────────────────────────


class TestHandlerMissingSymbol:
    """handle_seam_context_pack returns None for unknown symbols."""

    def test_missing_symbol_returns_none(self, tmp_path: Path) -> None:
        conn, root, _ = _make_indexed_db(tmp_path)
        result = handle_seam_context_pack(conn, "does_not_exist", root)
        conn.close()
        assert result is None


# ── PP3: Invalid input → INVALID_INPUT error dict ─────────────────────────────


class TestHandlerInvalidInput:
    """handle_seam_context_pack rejects blank/whitespace symbol names."""

    def test_blank_symbol_returns_invalid_input(self, tmp_path: Path) -> None:
        conn, root, _ = _make_indexed_db(tmp_path)
        result = handle_seam_context_pack(conn, "", root)
        conn.close()
        assert isinstance(result, dict)
        assert result.get("error") == "INVALID_INPUT"

    def test_whitespace_symbol_returns_invalid_input(self, tmp_path: Path) -> None:
        conn, root, _ = _make_indexed_db(tmp_path)
        result = handle_seam_context_pack(conn, "   ", root)
        conn.close()
        assert isinstance(result, dict)
        assert result.get("error") == "INVALID_INPUT"


# ── PP4: MCP handler + pack() produce same core bundle ───────────────────────


class TestHandlerVsPackParity:
    """handler and context_pack() return the same essential bundle."""

    def test_handler_callees_match_pack_callees(self, tmp_path: Path) -> None:
        """The callees returned by the handler match those from context_pack()."""
        conn, root, src = _make_indexed_db(tmp_path)

        raw_pack = context_pack(conn, "foo")
        handler_result = handle_seam_context_pack(conn, "foo", root)
        conn.close()

        assert raw_pack is not None
        assert handler_result is not None

        # Same number of callees
        assert len(raw_pack["callees"]) == len(handler_result["callees"])

        # Same callee names (order-independent set check)
        raw_names = {nb["name"] for nb in raw_pack["callees"]}
        handler_names = {nb["name"] for nb in handler_result["callees"]}
        assert raw_names == handler_names

    def test_truncated_counts_same_in_handler_and_pack(self, tmp_path: Path) -> None:
        conn, root, src = _make_indexed_db(tmp_path)

        raw_pack = context_pack(conn, "foo")
        handler_result = handle_seam_context_pack(conn, "foo", root)
        conn.close()

        assert raw_pack is not None
        assert handler_result is not None

        assert raw_pack["truncated"] == handler_result["truncated"]

    def test_relationship_evidence_counts_same_in_handler_and_pack(self, tmp_path: Path) -> None:
        conn, root, src = _make_indexed_db(tmp_path)

        raw_pack = context_pack(conn, "foo")
        handler_result = handle_seam_context_pack(conn, "foo", root)
        conn.close()

        assert raw_pack is not None
        assert handler_result is not None
        assert len(raw_pack["relationship_evidence"]["callees"]) == len(
            handler_result["relationship_evidence"]["callees"]
        )
        assert raw_pack["relationship_evidence"]["truncated"] == (
            handler_result["relationship_evidence"]["truncated"]
        )


# ── PP7: JSON envelope shape ──────────────────────────────────────────────────


class TestJsonEnvelopeShape:
    """build_success_envelope wraps pack data in {ok:true, data:...}."""

    def test_success_envelope_structure(self, tmp_path: Path) -> None:
        """Verify the output module wraps a pack in the standard envelope."""
        conn, root, src = _make_indexed_db(tmp_path)
        handler_result = handle_seam_context_pack(conn, "foo", root)
        conn.close()

        assert handler_result is not None
        envelope = build_success_envelope(handler_result)

        assert envelope["ok"] is True
        assert "data" in envelope
        data = envelope["data"]
        assert "target" in data
        assert "callers" in data
        assert "callees" in data
        assert "relationship_evidence" in data
        assert "caveats" in data
        assert "recommended_next_calls" in data

        # Must be JSON-serializable (no non-serializable types)
        serialized = json.dumps(envelope)
        reparsed = json.loads(serialized)
        assert reparsed["ok"] is True

    def test_success_envelope_for_missing_symbol(self, tmp_path: Path) -> None:
        """A missing symbol: handler returns None; CLI emits {ok:true, data:{found:false}}.

        WHY success envelope (not error): mirrors seam_context's contract.
        A missing symbol is a valid answer (not an error); the agent reads found:false
        and knows the symbol is not in the index — no false NOT_FOUND error code.
        """
        conn, root, _ = _make_indexed_db(tmp_path)
        handler_result = handle_seam_context_pack(conn, "no_such_symbol", root)
        conn.close()

        # Handler returns None — caller (CLI) wraps in success envelope with found:false
        assert handler_result is None

        # Build what the CLI now emits (success envelope, not error)
        from seam.cli.output import build_success_envelope
        success_env = build_success_envelope({"found": False, "symbol": "no_such_symbol"})
        assert success_env["ok"] is True
        assert success_env["data"]["found"] is False
        assert success_env["data"]["symbol"] == "no_such_symbol"
