"""Integration tests for Phase 6 — per-file cap and ambiguous-target behavior.

Tests the homonym-diversity behavior of context_pack():
  - When many same-named neighbors come from one file, per-file cap holds.
  - An ambiguous target (multiple symbols with same name) surfaces ambiguous=True.
  - The bundle stays diverse across files even when one file has many same-named symbols.

Test groups:
    PH1 — per-file cap holds when one file has many same-named neighbors
    PH2 — bundle stays diverse: entries from other files are not crowded out
    PH3 — ambiguous target → target.ambiguous == True in the bundle
    PH4 — truncated count reflects per-file-dropped + global-limit-dropped
"""

from pathlib import Path

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


# ── PH1: Per-file cap holds ────────────────────────────────────────────────────


class TestPerFileCapHolds:
    """SEAM_PACK_PER_FILE_CAP limits entries from any single file."""

    def test_per_file_cap_on_callees(self, tmp_path: Path) -> None:
        """Callees from one file are capped at SEAM_PACK_PER_FILE_CAP."""
        import seam.config as config

        conn = init_db(tmp_path / "test.db")
        hot = tmp_path / "hot.py"
        main = tmp_path / "main.py"
        hot.write_text("# hot\n")
        main.write_text("# main\n")

        # hot.py: many symbols (more than PER_FILE_CAP)
        n_hot = config.SEAM_PACK_PER_FILE_CAP + 4
        hot_syms = [_sym(f"hot_{i}", str(hot), line=1 + i) for i in range(n_hot)]
        upsert_file(conn, hot, "python", "h_hot", hot_syms, [])

        # main.py: foo calls all hot symbols
        main_syms = [_sym("foo", str(main), line=1)]
        main_edges = [_edge("foo", f"hot_{i}", str(main)) for i in range(n_hot)]
        upsert_file(conn, main, "python", "h_main", main_syms, main_edges)

        result = context_pack(conn, "foo")
        conn.close()

        assert result is not None
        hot_callees = [nb for nb in result["callees"] if nb["file"] == str(hot)]
        assert len(hot_callees) <= config.SEAM_PACK_PER_FILE_CAP

    def test_per_file_cap_on_callers(self, tmp_path: Path) -> None:
        """Callers from one file are capped at SEAM_PACK_PER_FILE_CAP."""
        import seam.config as config

        conn = init_db(tmp_path / "test.db")
        hot = tmp_path / "hot.py"
        tgt = tmp_path / "target.py"
        hot.write_text("# hot\n")
        tgt.write_text("# target\n")

        # target.py: defines the symbol we'll look up
        upsert_file(conn, tgt, "python", "h_tgt", [_sym("target", str(tgt), line=1)], [])

        # hot.py: many callers of target (more than PER_FILE_CAP)
        n_hot = config.SEAM_PACK_PER_FILE_CAP + 3
        hot_syms = [_sym(f"caller_{i}", str(hot), line=1 + i) for i in range(n_hot)]
        hot_edges = [_edge(f"caller_{i}", "target", str(hot)) for i in range(n_hot)]
        upsert_file(conn, hot, "python", "h_hot", hot_syms, hot_edges)

        result = context_pack(conn, "target")
        conn.close()

        assert result is not None
        hot_callers = [nb for nb in result["callers"] if nb["file"] == str(hot)]
        assert len(hot_callers) <= config.SEAM_PACK_PER_FILE_CAP


# ── PH2: Bundle stays diverse ─────────────────────────────────────────────────


class TestBundleDiversity:
    """Per-file cap keeps bundle diverse; other-file entries are not crowded out."""

    def test_diverse_callees_from_multiple_files(self, tmp_path: Path) -> None:
        """When one file is capped, entries from other files still appear."""
        import seam.config as config

        conn = init_db(tmp_path / "test.db")
        hot = tmp_path / "hot.py"
        cool = tmp_path / "cool.py"
        main = tmp_path / "main.py"
        hot.write_text("# hot\n")
        cool.write_text("# cool\n")
        main.write_text("# main\n")

        # hot.py: more than PER_FILE_CAP symbols
        n_hot = config.SEAM_PACK_PER_FILE_CAP + 2
        hot_syms = [_sym(f"hot_{i}", str(hot), line=1 + i) for i in range(n_hot)]
        upsert_file(conn, hot, "python", "h_hot", hot_syms, [])

        # cool.py: 1 symbol
        upsert_file(conn, cool, "python", "h_cool", [_sym("cool_fn", str(cool), line=1)], [])

        # main.py: foo calls all hot + cool
        main_syms = [_sym("foo", str(main), line=1)]
        main_edges = [_edge("foo", f"hot_{i}", str(main)) for i in range(n_hot)]
        main_edges.append(_edge("foo", "cool_fn", str(main)))
        upsert_file(conn, main, "python", "h_main", main_syms, main_edges)

        result = context_pack(conn, "foo")
        conn.close()

        assert result is not None
        # cool_fn from cool.py must appear (not crowded out by hot.py)
        callee_names = {nb["name"] for nb in result["callees"]}
        assert "cool_fn" in callee_names

        # hot.py entries are capped
        hot_callees = [nb for nb in result["callees"] if nb["file"] == str(hot)]
        assert len(hot_callees) <= config.SEAM_PACK_PER_FILE_CAP


# ── PH3: Ambiguous target ─────────────────────────────────────────────────────


class TestAmbiguousTarget:
    """When two files declare the same symbol name, target.ambiguous == True."""

    def test_ambiguous_flag_set_when_name_collision(self, tmp_path: Path) -> None:
        """target.ambiguous is True when the symbol name appears in multiple files."""
        conn = init_db(tmp_path / "test.db")

        a = tmp_path / "a.py"
        b = tmp_path / "b.py"
        a.write_text("def parse(): pass\n")
        b.write_text("def parse(): pass\n")

        upsert_file(conn, a, "python", "h_a", [_sym("parse", str(a), line=1)], [])
        upsert_file(conn, b, "python", "h_b", [_sym("parse", str(b), line=1)], [])

        result = context_pack(conn, "parse")
        conn.close()

        assert result is not None
        assert result["target"]["ambiguous"] is True

    def test_non_ambiguous_flag_for_unique_name(self, tmp_path: Path) -> None:
        """target.ambiguous is False for a symbol that only appears once."""
        conn = init_db(tmp_path / "test.db")

        a = tmp_path / "a.py"
        a.write_text("def unique_fn(): pass\n")
        upsert_file(conn, a, "python", "h_a", [_sym("unique_fn", str(a), line=1)], [])

        result = context_pack(conn, "unique_fn")
        conn.close()

        assert result is not None
        assert result["target"]["ambiguous"] is False

    def test_ambiguous_target_bundle_handler(self, tmp_path: Path) -> None:
        """handle_seam_context_pack surfaces ambiguous=True in the bundle."""
        conn = init_db(tmp_path / "test.db")
        root = tmp_path

        a = tmp_path / "a.py"
        b = tmp_path / "b.py"
        a.write_text("def helper(): pass\n")
        b.write_text("def helper(): pass\n")

        upsert_file(conn, a, "python", "h_a", [_sym("helper", str(a), line=1)], [])
        upsert_file(conn, b, "python", "h_b", [_sym("helper", str(b), line=1)], [])

        result = handle_seam_context_pack(conn, "helper", root)
        conn.close()

        assert result is not None
        assert result["target"]["ambiguous"] is True


# ── PH4: Truncated counts with per-file drops ─────────────────────────────────


class TestTruncatedWithPerFileCap:
    """truncated.callees reflects both per-file and global drops."""

    def test_truncated_reflects_per_file_drops(self, tmp_path: Path) -> None:
        """When per-file cap fires, truncated includes those dropped entries."""
        import seam.config as config

        conn = init_db(tmp_path / "test.db")
        hot = tmp_path / "hot.py"
        main = tmp_path / "main.py"
        hot.write_text("# hot\n")
        main.write_text("# main\n")

        n_hot = config.SEAM_PACK_PER_FILE_CAP + 2
        hot_syms = [_sym(f"hot_{i}", str(hot), line=1 + i) for i in range(n_hot)]
        upsert_file(conn, hot, "python", "h_hot", hot_syms, [])

        main_syms = [_sym("foo", str(main), line=1)]
        main_edges = [_edge("foo", f"hot_{i}", str(main)) for i in range(n_hot)]
        upsert_file(conn, main, "python", "h_main", main_syms, main_edges)

        result = context_pack(conn, "foo")
        conn.close()

        assert result is not None
        # Only PER_FILE_CAP callees should come through
        assert len(result["callees"]) <= config.SEAM_PACK_PER_FILE_CAP
        # truncated.callees must reflect the extra dropped entries
        expected_dropped = n_hot - config.SEAM_PACK_PER_FILE_CAP
        assert result["truncated"]["callees"] == expected_dropped
