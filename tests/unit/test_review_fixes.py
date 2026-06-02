"""Tests for review findings A-L (Phase 3 code review fixes).

TDD: these tests are written to be RED first, then fixed to GREEN.

Findings covered:
  A  - changes --quiet / rich crashes on error dict (KeyError)
  B  - LIKE fallback does not escape metacharacters (%, _, backslash)
  C  - affected rich/quiet silently swallows errors
  D  - fuzzy fallback candidate pool is arbitrary (not deterministic, not length-filtered)
  E  - unbounded affected work (file cap, symbol cap, partial flag)
  F  - changes --stdin guard condition buggy
  G  - single-char queries silently dropped (min term len 2 → 1)
  H  - observability logging (tested indirectly via behaviour, not log capture)
  I  - duplicate tokenizer (extract_terms now public in fts.py)
  J  - dead code _cluster_id_for_rows removed
  L  - contract fixes (mutual exclusion, docstring honesty, OperationalError → INVALID_QUERY)
"""

import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from seam.indexer.db import init_db, upsert_file
from seam.indexer.graph import Edge, Symbol

runner = CliRunner()


# ── Shared helpers ─────────────────────────────────────────────────────────────


def _sym(name: str, file: str, start: int = 1, end: int = 5) -> Symbol:
    return Symbol(
        name=name, kind="function", file=file, start_line=start, end_line=end, docstring=None
    )


def _edge(source: str, target: str, file: str, kind: str = "call") -> Edge:
    return Edge(source=source, target=target, kind=kind, file=file, line=1, confidence="EXTRACTED")


def _make_seeded_db(tmp_path: Path) -> tuple[Path, Path]:
    """Return (db_dir, project_root) with minimal seeded DB."""
    db_dir = tmp_path / "db"
    db_dir.mkdir()
    db_path = db_dir / ".seam" / "seam.db"
    db_path.parent.mkdir(parents=True)

    src = tmp_path / "src.py"
    src.write_text("def A(): pass\n")

    conn = init_db(db_path)
    upsert_file(conn, src, "python", "h1", [_sym("A", str(src))], [])
    conn.commit()
    conn.close()
    return db_dir, tmp_path


# ═══════════════════════════════════════════════════════════════════════════════
# Finding A — changes --quiet crashes on error dict (KeyError on "risk_level")
# ═══════════════════════════════════════════════════════════════════════════════


class TestFindingA:
    """changes_cmd must surface errors in ALL output modes (--json, --quiet, default)."""

    def test_changes_quiet_outside_git_repo_exits_nonzero(self, tmp_path: Path) -> None:
        """changes --quiet outside a git repo must not raise KeyError; must exit non-zero."""
        db_dir, proj = _make_seeded_db(tmp_path)
        from seam.cli.main import app

        result = runner.invoke(
            app,
            ["changes", "--quiet", "--db-dir", str(db_dir), "--path", str(proj)],
            catch_exceptions=False,
        )
        # Must exit non-zero (error, not crash)
        assert result.exit_code != 0, (
            f"Expected non-zero exit for --quiet outside git repo, got {result.exit_code}; "
            f"output: {result.output!r}"
        )
        # Must NOT print 'risk_level' (would mean it fell through to the happy path)
        assert "risk_level" not in result.output

    def test_changes_json_outside_git_repo_exits_nonzero_with_envelope(
        self, tmp_path: Path
    ) -> None:
        """changes --json outside a git repo must return a JSON error envelope + exit 1."""
        db_dir, proj = _make_seeded_db(tmp_path)
        from seam.cli.main import app

        result = runner.invoke(
            app,
            ["changes", "--json", "--db-dir", str(db_dir), "--path", str(proj)],
            catch_exceptions=False,
        )
        assert result.exit_code == 1
        envelope = json.loads(result.output)
        assert envelope["ok"] is False
        assert "error" in envelope

    def test_changes_default_outside_git_repo_exits_nonzero(self, tmp_path: Path) -> None:
        """changes (default/rich) outside a git repo must exit non-zero, no crash."""
        db_dir, proj = _make_seeded_db(tmp_path)
        from seam.cli.main import app

        result = runner.invoke(
            app,
            ["changes", "--db-dir", str(db_dir), "--path", str(proj)],
            catch_exceptions=False,
        )
        assert result.exit_code != 0


# ═══════════════════════════════════════════════════════════════════════════════
# Finding B — LIKE fallback metacharacter injection
# ═══════════════════════════════════════════════════════════════════════════════


class TestFindingB:
    """_like_fallback must escape %, _, and backslash in the term before building LIKE %term%."""

    def _make_db_with_symbols(self, tmp_path: Path, names: list[str]) -> sqlite3.Connection:
        """Build an in-memory DB with the given symbol names."""
        db_path = tmp_path / "test.db"
        conn = init_db(db_path)
        src = tmp_path / "src.py"
        src.write_text("# stub\n")
        syms = [_sym(n, str(src), i + 1, i + 2) for i, n in enumerate(names)]
        upsert_file(conn, src, "python", "h1", syms, [])
        return conn

    def test_underscore_in_term_matches_literal_only(self, tmp_path: Path) -> None:
        """A term containing '_' should only match names with a literal underscore.

        Before the fix: LIKE '%get_user%' treats '_' as single-char wildcard,
        so 'getXuser' matches too. After the fix, it must not.

        We seed 'get_user' (has literal underscore) and 'getXuser' (no underscore).
        Only 'get_user' should be returned for the term 'get_user'.
        """
        conn = self._make_db_with_symbols(tmp_path, ["get_user", "getXuser"])
        from seam.query.engine import _like_fallback

        rows = _like_fallback(conn, "get_user", limit=20)
        conn.close()

        names = [r["symbol"] for r in rows]
        assert "get_user" in names, "get_user should match the literal-underscore query"
        assert "getXuser" not in names, (
            "getXuser must NOT match — underscore should be escaped, not treated as wildcard"
        )

    def test_percent_in_term_matches_literal_only(self, tmp_path: Path) -> None:
        """A term containing '%' should only match names with a literal percent.

        Before fix: LIKE '%%pct%%' matches everything (% is SQL wildcard).
        After fix: only 'func%pct' (with literal %) should match.
        """
        conn = self._make_db_with_symbols(tmp_path, ["func_pct", "funcXpct"])
        from seam.query.engine import _like_fallback

        # Search for a term that contains a literal '%'
        rows = _like_fallback(conn, "func%pct", limit=20)
        conn.close()

        # After escaping, '%' in the term is treated as literal.
        # 'func%pct' cannot be in the DB (% is invalid in symbol names in most languages),
        # so no results; the key assertion is: no crash and result is a list.
        assert isinstance(rows, list)
        # 'funcXpct' must NOT be returned (would indicate unescaped wildcard)
        names = [r["symbol"] for r in rows]
        assert "funcXpct" not in names, (
            "funcXpct must not match — the '%' in the query term should be a literal match"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Finding C — affected rich/quiet silently swallows errors
# ═══════════════════════════════════════════════════════════════════════════════


class TestFindingC:
    """affected_cmd must surface errors in --quiet and rich modes (not silently fail)."""

    def test_affected_quiet_with_error_result_exits_nonzero(self, tmp_path: Path) -> None:
        """When handle_seam_affected returns an error dict, --quiet must exit non-zero."""
        db_dir, proj = _make_seeded_db(tmp_path)
        from seam.cli.main import app

        # Patch handle_seam_affected to return an error dict (simulates INVALID_INPUT)
        error_dict = {"error": "INVALID_INPUT", "message": "test error message"}
        with patch("seam.cli.main.handle_seam_affected", return_value=error_dict):
            result = runner.invoke(
                app,
                [
                    "affected",
                    "src.py",
                    "--quiet",
                    "--db-dir",
                    str(db_dir),
                    "--path",
                    str(proj),
                ],
                catch_exceptions=False,
            )
        assert result.exit_code != 0, (
            f"Expected non-zero exit for error dict in --quiet mode, got {result.exit_code}"
        )

    def test_affected_rich_with_error_result_exits_nonzero(self, tmp_path: Path) -> None:
        """When handle_seam_affected returns an error dict, rich mode must exit non-zero."""
        db_dir, proj = _make_seeded_db(tmp_path)
        from seam.cli.main import app

        error_dict = {"error": "INVALID_INPUT", "message": "test error from rich"}
        with patch("seam.cli.main.handle_seam_affected", return_value=error_dict):
            result = runner.invoke(
                app,
                [
                    "affected",
                    "src.py",
                    "--db-dir",
                    str(db_dir),
                    "--path",
                    str(proj),
                ],
                catch_exceptions=False,
            )
        assert result.exit_code != 0, (
            f"Expected non-zero exit for error dict in rich mode, got {result.exit_code}"
        )
        # Must not print "No affected test files found" as if it succeeded
        assert "No affected test files found" not in result.output

    def test_affected_json_with_error_result_exits_nonzero(self, tmp_path: Path) -> None:
        """When handle_seam_affected returns an error dict, --json must exit 1 with envelope."""
        db_dir, proj = _make_seeded_db(tmp_path)
        from seam.cli.main import app

        error_dict = {"error": "INVALID_INPUT", "message": "test json error"}
        with patch("seam.cli.main.handle_seam_affected", return_value=error_dict):
            result = runner.invoke(
                app,
                [
                    "affected",
                    "src.py",
                    "--json",
                    "--db-dir",
                    str(db_dir),
                    "--path",
                    str(proj),
                ],
                catch_exceptions=False,
            )
        assert result.exit_code == 1
        envelope = json.loads(result.output)
        assert envelope["ok"] is False


# ═══════════════════════════════════════════════════════════════════════════════
# Finding D — fuzzy fallback determinism and length filtering
# ═══════════════════════════════════════════════════════════════════════════════


class TestFindingD:
    """_fuzzy_fallback must be deterministic and filter by length window."""

    def _make_db(self, tmp_path: Path, names: list[str]) -> sqlite3.Connection:
        db_path = tmp_path / "fuzz.db"
        conn = init_db(db_path)
        src = tmp_path / "s.py"
        src.write_text("# stub\n")
        syms = [_sym(n, str(src), i * 10, i * 10 + 5) for i, n in enumerate(names)]
        upsert_file(conn, src, "python", "h1", syms, [])
        return conn

    def test_fuzzy_fallback_is_deterministic(self, tmp_path: Path) -> None:
        """_fuzzy_fallback returns same result across multiple calls (ORDER BY name)."""
        # Seed enough names that ordering matters
        names = [f"sym_{i:04d}" for i in range(50)] + ["authenticate_user"]
        conn = self._make_db(tmp_path, names)
        from seam.query.engine import _fuzzy_fallback

        results_a = [
            r["symbol"]
            for r in _fuzzy_fallback(
                conn, "authenticate_user", max_dist=1, candidate_cap=200, limit=20
            )
        ]
        results_b = [
            r["symbol"]
            for r in _fuzzy_fallback(
                conn, "authenticate_user", max_dist=1, candidate_cap=200, limit=20
            )
        ]
        conn.close()

        assert results_a == results_b, (
            f"_fuzzy_fallback is non-deterministic: first={results_a}, second={results_b}"
        )

    def test_fuzzy_fallback_length_filter_excludes_distant_lengths(self, tmp_path: Path) -> None:
        """_fuzzy_fallback pre-filters by length so very long/short names are excluded.

        With max_dist=1 and term='abc' (len=3), names of length <= 1 or >= 6
        can never have edit-distance <= 1, so they should be excluded even before
        the full DL computation.
        """
        # 'abc' is len 3; 'a' is len 1 (diff=2, cannot match dist=1)
        # 'abcdefgh' is len 8 (diff=5, cannot match)
        # 'abd' is len 3, dist=1 from 'abc' → should match
        names = ["a", "abc_defghij", "abd", "abc"]
        conn = self._make_db(tmp_path, names)
        from seam.query.engine import _fuzzy_fallback

        results = _fuzzy_fallback(conn, "abc", max_dist=1, candidate_cap=1000, limit=20)
        conn.close()

        found_names = {r["symbol"] for r in results}
        assert "abc" in found_names, "'abc' (dist=0) should be found"
        assert "abd" in found_names, "'abd' (dist=1) should be found"
        # 'a' has length 1; |3-1|=2 > max_dist=1 → must be excluded by length filter
        assert "a" not in found_names, "'a' is too short to match dist=1 from 'abc'"


# ═══════════════════════════════════════════════════════════════════════════════
# Finding E — unbounded affected work
# ═══════════════════════════════════════════════════════════════════════════════


class TestFindingE:
    """handle_seam_affected must reject oversized file lists; affected() must cap symbols."""

    def test_affected_handler_rejects_oversized_file_list(self, tmp_path: Path) -> None:
        """handle_seam_affected must return INVALID_INPUT when len(changed_files) > cap."""
        import seam.config as config
        from seam.server.tools import handle_seam_affected

        db_path = tmp_path / ".seam" / "seam.db"
        db_path.parent.mkdir(parents=True)
        conn = init_db(db_path)

        # Build a file list exceeding the cap
        cap = config.SEAM_MAX_AFFECTED_FILES
        oversized = [f"file_{i}.py" for i in range(cap + 1)]

        result = handle_seam_affected(conn, oversized, tmp_path, depth=3)
        conn.close()

        assert isinstance(result, dict)
        assert result.get("error") == "INVALID_INPUT", (
            f"Expected INVALID_INPUT for oversized file list (>{cap} files), got: {result}"
        )

    def test_affected_partial_flag_set_when_symbol_cap_hit(self, tmp_path: Path) -> None:
        """affected() sets partial=True when the per-file symbol cap is exceeded."""
        import seam.config as config
        from seam.analysis.affected import affected

        db_path = tmp_path / ".seam" / "seam.db"
        db_path.parent.mkdir(parents=True)
        conn = init_db(db_path)

        src = tmp_path / "big_file.py"
        src.write_text("# big file\n")

        # Seed more symbols than the cap allows per file
        cap = config.SEAM_MAX_AFFECTED_SYMBOLS
        syms = [_sym(f"func_{i}", str(src), i * 5, i * 5 + 4) for i in range(cap + 5)]
        upsert_file(conn, src, "python", "h1", syms, [])

        result = affected(conn, [str(src)], depth=1, repo_root=tmp_path)
        conn.close()

        # partial should be True because we exceeded the symbol cap
        assert result.get("partial") is True, (
            f"Expected partial=True when symbol cap ({cap}) exceeded, got: {result}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Finding F — changes --stdin guard condition
# ═══════════════════════════════════════════════════════════════════════════════


class TestFindingF:
    """changes --stdin narrows changed_symbols but leaves risk_level from full diff."""

    def test_changes_stdin_skipped_when_error_report(self, tmp_path: Path) -> None:
        """When handle_seam_changes returns an error dict, stdin filter is not applied."""
        db_dir, proj = _make_seeded_db(tmp_path)
        from seam.cli.main import app

        error_report = {"error": "NOT_A_GIT_REPO", "message": "not a git repo"}
        with patch("seam.cli.main.handle_seam_changes", return_value=error_report):
            result = runner.invoke(
                app,
                ["changes", "--json", "--stdin", "--db-dir", str(db_dir), "--path", str(proj)],
                input="src.py\n",
                catch_exceptions=False,
            )
        # Should exit non-zero with error envelope, not crash
        assert result.exit_code != 0
        envelope = json.loads(result.output)
        assert envelope["ok"] is False


# ═══════════════════════════════════════════════════════════════════════════════
# Finding G — single-char query regression (min term len 2 → 1)
# ═══════════════════════════════════════════════════════════════════════════════


class TestFindingG:
    """Single-char queries must return results; operator-only queries still return []."""

    def _make_db_with_symbol_x(self, tmp_path: Path) -> sqlite3.Connection:
        db_path = tmp_path / "g.db"
        conn = init_db(db_path)
        src = tmp_path / "s.py"
        src.write_text("# stub\n")
        upsert_file(conn, src, "python", "h1", [_sym("x", str(src))], [])
        return conn

    def test_search_single_char_symbol_found(self, tmp_path: Path) -> None:
        """search('x') must find a symbol named 'x' (single-char term must not be dropped)."""
        conn = self._make_db_with_symbol_x(tmp_path)
        from seam.query.engine import search

        results = search(conn, "x")
        conn.close()

        names = [r["symbol"] for r in results]
        assert "x" in names, (
            f"Expected symbol 'x' in search('x') results; got {names}. "
            "Single-char queries must not be dropped by _MIN_TERM_LEN filter."
        )

    def test_build_match_query_single_char_not_dropped(self) -> None:
        """build_match_query('x') must include the single-char term, not strip it."""
        from seam.query.fts import build_match_query

        result = build_match_query("x")
        # Should not be empty sentinel
        assert result != "", "Single-char term 'x' must not be stripped from match query"
        assert '"x"*' in result, f"Expected quoted prefix '\"x\"*' in result, got: {result!r}"

    def test_build_match_query_operator_only_returns_empty_sentinel(self) -> None:
        """Operator-only queries ('AND OR NOT') still return the empty sentinel."""
        from seam.query.fts import build_match_query

        result = build_match_query("AND OR NOT")
        assert result == "", f"Operator-only query must return empty sentinel, got: {result!r}"

    def test_fuzzy_min_term_stays_at_3(self) -> None:
        """Fuzzy fallback still skips terms shorter than 3 chars (noise prevention)."""
        conn = init_db(Path(":memory:"))
        from seam.query.engine import search

        # 'xy' is 2 chars — fuzzy should NOT be attempted on it (too noisy)
        # This is a structural assertion: if it returns results, they must come from
        # LIKE/FTS5, NOT fuzzy. We just assert no crash.
        results = search(conn, "xy")
        conn.close()
        assert isinstance(results, list)  # no crash


# ═══════════════════════════════════════════════════════════════════════════════
# Finding I — single-source tokenizer: extract_terms public in fts.py
# ═══════════════════════════════════════════════════════════════════════════════


class TestFindingI:
    """extract_terms must be importable from seam.query.fts and work correctly."""

    def test_extract_terms_importable_from_fts(self) -> None:
        """extract_terms must be exported from seam.query.fts."""
        from seam.query.fts import extract_terms  # noqa: F401 — import is the assertion

    def test_extract_terms_basic(self) -> None:
        """extract_terms returns a list of plain tokens from user text."""
        from seam.query.fts import extract_terms

        terms = extract_terms("parse issues board")
        assert isinstance(terms, list)
        assert "parse" in terms
        assert "issues" in terms
        assert "board" in terms

    def test_extract_terms_strips_fts5_operators(self) -> None:
        """extract_terms removes FTS5 operators from the term list."""
        from seam.query.fts import extract_terms

        terms = extract_terms("parse AND issues OR NOT NEAR board")
        assert "AND" not in terms
        assert "OR" not in terms
        assert "NOT" not in terms
        assert "NEAR" not in terms

    def test_engine_uses_fts_extract_terms(self) -> None:
        """engine._extract_terms and fts.extract_terms produce identical output.

        This guards the 'single source of truth' requirement: both must agree.
        """
        from seam.query.engine import _extract_terms as engine_extract
        from seam.query.fts import extract_terms as fts_extract

        test_inputs = [
            "parse issues board",
            "authenticate AND user",
            "x y z",
            "get_user",
            "",
        ]
        for text in test_inputs:
            assert fts_extract(text) == engine_extract(text), (
                f"fts.extract_terms and engine._extract_terms disagree for {text!r}: "
                f"fts={fts_extract(text)}, engine={engine_extract(text)}"
            )


# ═══════════════════════════════════════════════════════════════════════════════
# Finding J — dead code _cluster_id_for_rows removed
# ═══════════════════════════════════════════════════════════════════════════════


class TestFindingJ:
    """_cluster_id_for_rows must be removed from engine.py."""

    def test_cluster_id_for_rows_not_in_engine(self) -> None:
        """engine.py must not expose _cluster_id_for_rows (dead code removed)."""
        import seam.query.engine as eng

        assert not hasattr(eng, "_cluster_id_for_rows"), (
            "_cluster_id_for_rows is dead code and must be removed from engine.py"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Finding L — small contract fixes
# ═══════════════════════════════════════════════════════════════════════════════


class TestFindingL:
    """L1: affected positional + --stdin mutual exclusion.
    L2: total_dependents_traversed docstring honesty.
    L3: OperationalError → INVALID_QUERY mapping still works.
    """

    def test_affected_positional_and_stdin_mutual_exclusion(self, tmp_path: Path) -> None:
        """Providing both positional files AND --stdin must produce an error, not silently ignore."""
        db_dir, proj = _make_seeded_db(tmp_path)
        from seam.cli.main import app

        result = runner.invoke(
            app,
            [
                "affected",
                "src.py",  # positional
                "--stdin",  # also --stdin
                "--db-dir",
                str(db_dir),
                "--path",
                str(proj),
            ],
            input="other.py\n",
            catch_exceptions=False,
        )
        # Must error — cannot use both positional and --stdin
        assert result.exit_code != 0, (
            f"Expected non-zero exit when both positional args and --stdin are given, "
            f"got exit_code={result.exit_code}"
        )

    def test_operational_error_maps_to_invalid_query(self) -> None:
        """A genuine FTS5 OperationalError must still map to INVALID_QUERY in the handler.

        This guards the path: search() propagates OperationalError →
        handle_seam_search() catches it → INVALID_QUERY dict returned.
        """
        from seam.indexer.db import init_db
        from seam.server.tools import handle_seam_search

        conn = init_db(Path(":memory:"))
        # Inject a genuinely malformed FTS5 expression by patching build_match_query
        # to return something that SQLite's FTS5 parser will reject.
        with patch(
            "seam.query.engine.fts.build_match_query", return_value='"MATCH SYNTAX ERROR{{{'
        ):
            result = handle_seam_search(conn, "anything", Path("/project"))
        conn.close()

        assert isinstance(result, dict), f"Expected error dict, got: {result}"
        assert result.get("error") == "INVALID_QUERY", (
            f"Expected INVALID_QUERY for OperationalError, got: {result}"
        )

    def test_affected_total_dependents_may_double_count_docstring(self) -> None:
        """Structural test: total_dependents_traversed may count same dependent multiple times.

        This is the honest behavior (not deduplicated across symbols from the same file).
        We verify the field exists and is an int >= 0.
        """
        from seam.analysis.affected import AffectedResult

        # AffectedResult must have total_dependents_traversed as int field
        assert "total_dependents_traversed" in AffectedResult.__annotations__
        assert AffectedResult.__annotations__["total_dependents_traversed"] is int
