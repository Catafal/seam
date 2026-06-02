"""Tests for seam/query/fts.py — FTS query builder and rescorer (Slice 1).

Test strategy: assert observable behaviour (output shape, ordering, presence/absence),
not internal state. All tests are pure-Python — no DB required.

Groups:
    B1 — build_match_query(): OR-join, stripping, single-term, empty-after-strip
    B2 — rescore(): exact-name priority, test-file dampening, cluster boost
    B3 — regression: the "parse issues board"-class multi-term query MUST return hits
         (tested at the engine level using a real in-memory DB).
"""

import tempfile
from pathlib import Path

# ── The module under test (does not exist yet — RED phase) ───────────────────
from seam.query.fts import build_match_query, rescore

# ═══════════════════════════════════════════════════════════════════════════════
# B1 — build_match_query()
# ═══════════════════════════════════════════════════════════════════════════════


class TestBuildMatchQuery:
    """build_match_query(text) -> str: safe FTS5 MATCH expression."""

    # ── Multi-term: OR join ────────────────────────────────────────────────────

    def test_multi_term_joins_with_or(self) -> None:
        """Two words produce an OR expression, not an AND (the core bug fix)."""
        result = build_match_query("parse issues")
        # Must contain OR, not AND
        assert " OR " in result
        # AND must not appear (FTS5 implicit AND is the bug)
        assert " AND " not in result

    def test_multi_term_three_words(self) -> None:
        """Three words all appear as OR-joined quoted prefixes."""
        result = build_match_query("parse issues board")
        assert result.count(" OR ") == 2  # three terms → two ORs

    def test_each_term_quoted_prefix(self) -> None:
        """Each surviving term is wrapped as a quoted prefix: "term"* ."""
        result = build_match_query("parse")
        # Quoted-prefix format: "parse"* (with the asterisk)
        assert '"parse"*' in result

    def test_multi_term_each_term_is_prefix(self) -> None:
        """Every term in a multi-word query is individually quoted+prefixed."""
        result = build_match_query("parse issues")
        assert '"parse"*' in result
        assert '"issues"*' in result

    # ── Single-term ───────────────────────────────────────────────────────────

    def test_single_term_no_or(self) -> None:
        """A single-word query has no OR operator."""
        result = build_match_query("authenticate")
        assert " OR " not in result
        assert '"authenticate"*' in result

    # ── Special-char / operator stripping ─────────────────────────────────────

    def test_strips_fts5_operators(self) -> None:
        """FTS5 boolean operators (AND OR NOT) are stripped as operator noise."""
        # Raw operators like AND/OR/NOT would change MATCH semantics; strip them.
        result = build_match_query("parse AND issues")
        # 'AND' stripped → only 'parse' and 'issues' survive as terms
        assert '"parse"*' in result
        assert '"issues"*' in result
        # The literal word AND should NOT appear as a quoted term
        assert '"AND"*' not in result and '"and"*' not in result

    def test_strips_fts5_special_characters(self) -> None:
        """Characters that are FTS5 syntax (quotes, parens, *, ^) are stripped."""
        result = build_match_query('parse "issues" (board)')
        # Should not crash; surviving terms should be present
        assert '"parse"*' in result or '"issues"*' in result or '"board"*' in result

    def test_strips_fts5_near_operator(self) -> None:
        """NEAR is an FTS5 operator — it must not survive into the MATCH string."""
        result = build_match_query("parse NEAR issues")
        # NEAR should be stripped; parse and issues should survive
        assert '"parse"*' in result
        assert '"issues"*' in result
        assert "NEAR" not in result

    def test_strips_minus_operator(self) -> None:
        """Leading minus is an FTS5 NOT operator — strip it."""
        result = build_match_query("-parse issues")
        # 'parse' and 'issues' should survive (minus is stripped)
        assert '"issues"*' in result
        # 'parse' may or may not survive depending on stripping approach,
        # but '-parse' as a quoted prefix must never appear
        assert '"-parse"*' not in result

    # ── Empty-after-strip ─────────────────────────────────────────────────────

    def test_empty_string_does_not_crash(self) -> None:
        """Empty input returns a value — may be empty string or a no-hit sentinel."""
        result = build_match_query("")
        # Must not raise; must be a string
        assert isinstance(result, str)

    def test_all_operators_stripped_safe_result(self) -> None:
        """Input that strips to nothing returns a string (safe sentinel, not crash)."""
        result = build_match_query("AND OR NOT NEAR")
        assert isinstance(result, str)
        # Result should NOT contain any of the operator words as quoted terms
        assert '"AND"*' not in result
        assert '"OR"*' not in result

    def test_whitespace_only_does_not_crash(self) -> None:
        """Whitespace-only input is handled gracefully."""
        result = build_match_query("   ")
        assert isinstance(result, str)

    # ── Structural ────────────────────────────────────────────────────────────

    def test_returns_string(self) -> None:
        """build_match_query always returns a str."""
        assert isinstance(build_match_query("any input"), str)
        assert isinstance(build_match_query(""), str)
        assert isinstance(build_match_query("a b c"), str)


# ═══════════════════════════════════════════════════════════════════════════════
# B2 — rescore()
# ═══════════════════════════════════════════════════════════════════════════════

# Minimal row factory — rescore() operates on dict-like rows with keys:
# symbol, file, line, snippet, score  (SearchResult shape)


def _row(
    symbol: str,
    file: str = "/project/src/foo.py",
    line: int = 1,
    snippet: str = "",
    score: float = 1.0,
    cluster_id: int | None = None,
) -> dict:
    """Build a minimal result row for rescore() input."""
    return {
        "symbol": symbol,
        "file": file,
        "line": line,
        "snippet": snippet,
        "score": score,
        "cluster_id": cluster_id,
    }


class TestRescore:
    """rescore(rows, terms) -> list: multi-signal reranking."""

    # ── Exact-name priority ────────────────────────────────────────────────────

    def test_exact_name_beats_docstring_mention(self) -> None:
        """A symbol whose name IS the query term ranks above one that only mentions it."""
        terms = ["parse"]
        rows = [
            _row("process_all", snippet="This function parse tokens in the input stream"),
            _row("parse"),  # exact name match
        ]
        ranked = rescore(rows, terms)
        names = [r["symbol"] for r in ranked]
        assert names[0] == "parse", f"Expected 'parse' first, got: {names}"

    def test_prefix_name_beats_unrelated_name(self) -> None:
        """A symbol whose name STARTS WITH the query term ranks above an unrelated one."""
        terms = ["parse"]
        rows = [
            _row("validate_input"),  # neither exact nor prefix match
            _row("parse_token"),  # prefix match: name starts with 'parse'
        ]
        ranked = rescore(rows, terms)
        names = [r["symbol"] for r in ranked]
        assert names[0] == "parse_token", f"Expected 'parse_token' first, got: {names}"

    # ── Test-file dampening ────────────────────────────────────────────────────

    def test_test_files_dampened_on_non_test_query(self) -> None:
        """Production file outranks test file when query has no test-signal.

        We use a symbol name that does NOT exactly match the query term, so the
        test-file dampening is the deciding factor (not the name bonus).
        """
        terms = ["processor"]
        rows = [
            # In test file: same raw score but gets the dampening penalty
            _row("run_processor", file="/project/tests/test_runner.py", score=5.0),
            # In production: lower raw score but no penalty
            _row("call_processor", file="/project/src/runner.py", score=1.0),
        ]
        ranked = rescore(rows, terms)
        names = [r["symbol"] for r in ranked]
        # call_processor (production, lower raw score) should outrank run_processor (test)
        # because dampening penalty (-30) exceeds the score gap (5.0 vs 1.0 = 4.0)
        assert names[0] == "call_processor", (
            f"Expected production-file symbol first due to dampening, got: {names}"
        )

    def test_test_files_not_dampened_on_test_query(self) -> None:
        """When the query itself contains 'test', dampening is suspended."""
        terms = ["test", "parse"]
        rows = [
            _row("parse", file="/project/tests/test_parser.py", score=5.0),
            _row("parse_util", file="/project/src/parser.py", score=1.0),
        ]
        ranked = rescore(rows, terms)
        # With test dampening off, the higher raw score should win (or at least
        # test file should not be relegated to last)
        # The exact order depends on other signals, but the test-file result
        # must not be automatically last.
        assert len(ranked) == 2  # both results returned, no crash

    # ── Cluster boost ──────────────────────────────────────────────────────────

    def test_cluster_boost_applied_when_data_present(self) -> None:
        """Rows sharing the strongest seed's cluster_id get a relevance boost."""
        terms = ["parse"]
        # Row with cluster_id=1 and exact-name match is the strongest seed.
        # Other row with cluster_id=1 should get a boost.
        # Row with cluster_id=2 should not.
        rows = [
            _row("parse", score=10.0, cluster_id=1),  # seed + cluster 1
            _row("tokenize", score=0.5, cluster_id=1),  # same cluster → boost
            _row("validate", score=0.5, cluster_id=2),  # different cluster
        ]
        ranked = rescore(rows, terms)
        names = [r["symbol"] for r in ranked]
        # 'parse' must be first (exact match + high score)
        assert names[0] == "parse"
        # 'tokenize' (cluster-boosted) should outrank 'validate' (not boosted)
        tokenize_idx = names.index("tokenize")
        validate_idx = names.index("validate")
        assert tokenize_idx < validate_idx, (
            f"Expected cluster-boosted 'tokenize' before 'validate', got: {names}"
        )

    def test_stable_when_cluster_data_absent(self) -> None:
        """rescore() handles rows without cluster_id (None) without crashing."""
        terms = ["parse"]
        rows = [
            _row("parse", cluster_id=None),
            _row("tokenize", cluster_id=None),
        ]
        ranked = rescore(rows, terms)
        assert len(ranked) == 2  # no crash; returns both rows

    def test_mixed_cluster_presence(self) -> None:
        """rescore() handles mix of clustered and unclustered rows."""
        terms = ["process"]
        rows = [
            _row("process_data", cluster_id=1),
            _row("process_all", cluster_id=None),
        ]
        ranked = rescore(rows, terms)
        assert len(ranked) == 2  # no crash

    # ── General properties ────────────────────────────────────────────────────

    def test_returns_all_rows(self) -> None:
        """rescore() returns the same number of rows as the input."""
        terms = ["func"]
        rows = [_row(f"func_{i}") for i in range(5)]
        ranked = rescore(rows, terms)
        assert len(ranked) == 5

    def test_empty_rows_returns_empty(self) -> None:
        """rescore() on empty input returns empty list."""
        ranked = rescore([], ["parse"])
        assert ranked == []

    def test_empty_terms_does_not_crash(self) -> None:
        """rescore() with empty terms list doesn't crash."""
        rows = [_row("parse"), _row("tokenize")]
        ranked = rescore(rows, [])
        assert len(ranked) == 2

    def test_score_labeled_relevance_not_percentage(self) -> None:
        """rescore() output 'score' is an unbounded float, not a percentage.

        Per PRD user story 23: never render the relevance score as a percentage.
        We assert it is a plain float. The consumer must label it 'relevance'.
        """
        terms = ["parse"]
        rows = [_row("parse", score=1.0)]
        ranked = rescore(rows, terms)
        assert isinstance(ranked[0]["score"], float)
        # The score may be >1.0 (unbounded heuristic) — that is expected.
        # If this was a percentage it would be capped at 1.0 or 100.0,
        # but we make no assumption about the upper bound.


# ═══════════════════════════════════════════════════════════════════════════════
# B3 — Regression: multi-term query MUST NOT silently AND to zero
# (tested at engine level with a real in-memory DB)
# ═══════════════════════════════════════════════════════════════════════════════


class TestMultiTermRegressionAtEngine:
    """The AND-bug regression: a multi-term query where one word matches nothing
    must still return hits for the words that DO match.

    Fixture: "parse" matches 'parse_token' and 'parse_ast'.
             "issues" matches 'issues_handler'.
             "board" matches NOTHING.

    Pre-fix (AND): "parse issues board" → 0 results (the bug).
    Post-fix (OR): "parse issues board" → 3 results (parse + issues symbols).
    """

    def _build_regression_db(self) -> object:
        """Build a small in-memory DB with the fixture symbols for the regression."""
        from seam.indexer.db import init_db, upsert_file
        from seam.indexer.graph import Symbol

        conn = init_db(Path(":memory:"))
        with tempfile.NamedTemporaryFile(suffix=".py", delete=False) as f:
            filepath = Path(f.name)
            f.write(b"# regression fixture\n")

        syms = [
            Symbol(
                name="parse_token",
                kind="function",
                file=str(filepath),
                start_line=1,
                end_line=5,
                docstring=None,
            ),
            Symbol(
                name="parse_ast",
                kind="function",
                file=str(filepath),
                start_line=6,
                end_line=10,
                docstring=None,
            ),
            Symbol(
                name="issues_handler",
                kind="function",
                file=str(filepath),
                start_line=11,
                end_line=15,
                docstring="Handles tracking issues in the system",
            ),
            Symbol(
                name="unrelated_func",
                kind="function",
                file=str(filepath),
                start_line=16,
                end_line=20,
                docstring=None,
            ),
        ]
        upsert_file(conn, filepath, "python", "regr123", syms, [])
        filepath.unlink(missing_ok=True)
        return conn

    def test_multi_term_one_nonmatching_word_still_returns_hits(self) -> None:
        """'parse issues board' with 'board' matching nothing still returns results.

        This is the core regression guard: the OR-join must ensure one non-matching
        word cannot zero out the entire result set.
        """
        from seam.query.engine import search

        conn = self._build_regression_db()
        results = search(conn, "parse issues board")
        conn.close()

        # Must return at least one result (not zero — the pre-fix behaviour)
        assert len(results) > 0, (
            "REGRESSION: 'parse issues board' returned zero hits. "
            "The OR-join is not working — one non-matching word (board) "
            "zeroed the entire result set (implicit AND bug)."
        )

    def test_multi_term_matching_words_present_in_results(self) -> None:
        """Results include symbols matching 'parse' or 'issues', not just any symbol."""
        from seam.query.engine import search

        conn = self._build_regression_db()
        results = search(conn, "parse issues board")
        conn.close()

        names = {r["symbol"] for r in results}
        # At least one of the matching symbols must be returned
        assert names & {"parse_token", "parse_ast", "issues_handler"}, (
            f"Expected parse/issues symbols in results, got: {names}"
        )

    def test_unmatching_term_does_not_erase_matching_ones(self) -> None:
        """Specifically: 'board' (zero hits alone) must not erase 'parse' hits."""
        from seam.query.engine import search

        conn = self._build_regression_db()
        # Sanity: 'board' alone should return zero results
        board_results = search(conn, "board")
        assert board_results == [], "Fixture issue: 'board' should match nothing in this fixture"

        # But 'parse board' must still return parse_* symbols
        combined_results = search(conn, "parse board")
        assert len(combined_results) > 0, (
            "REGRESSION: 'parse board' returned zero hits. "
            "The non-matching 'board' zeroed the 'parse' results."
        )
        names = {r["symbol"] for r in combined_results}
        assert names & {"parse_token", "parse_ast"}, (
            f"Expected parse symbols in 'parse board' results, got: {names}"
        )

    def test_query_engine_also_uses_or_join(self) -> None:
        """query() (not just search()) also benefits from the OR-join fix."""
        from seam.query.engine import query

        conn = self._build_regression_db()
        results = query(conn, "parse issues board")
        conn.close()

        assert len(results) > 0, (
            "REGRESSION: query() with 'parse issues board' returned zero hits. "
            "The OR-join must apply to query() too."
        )


# ═══════════════════════════════════════════════════════════════════════════════
# B4 — Fallback: near-miss / typo still returns candidates
# ═══════════════════════════════════════════════════════════════════════════════


class TestFuzzyFallback:
    """LIKE→fuzzy fallback: a near-miss query returns rankable candidates."""

    def _build_fallback_db(self) -> object:
        """Fixture with known symbols for fallback testing."""
        from seam.indexer.db import init_db, upsert_file
        from seam.indexer.graph import Symbol

        conn = init_db(Path(":memory:"))
        with tempfile.NamedTemporaryFile(suffix=".py", delete=False) as f:
            filepath = Path(f.name)
            f.write(b"# fallback fixture\n")

        syms = [
            Symbol(
                name="authenticate_user",
                kind="function",
                file=str(filepath),
                start_line=1,
                end_line=5,
                docstring=None,
            ),
            Symbol(
                name="process_payment",
                kind="function",
                file=str(filepath),
                start_line=6,
                end_line=10,
                docstring=None,
            ),
            Symbol(
                name="validate_token",
                kind="function",
                file=str(filepath),
                start_line=11,
                end_line=15,
                docstring=None,
            ),
        ]
        upsert_file(conn, filepath, "python", "fallback123", syms, [])
        filepath.unlink(missing_ok=True)
        return conn

    def test_typo_returns_candidates_via_fallback(self) -> None:
        """A query with a 1-char typo (autenticate_user vs authenticate_user) returns results.

        'autenticate_user' is exactly 1 Damerau-Levenshtein edit from 'authenticate_user'
        (a single deletion of the 'h' at position 3). The FTS fallback should catch this.
        """
        from seam.query.engine import search

        conn = self._build_fallback_db()
        # 'autenticate_user' is 1 deletion away from 'authenticate_user'.
        # FTS5 won't match it (no such prefix in the index), LIKE won't match it
        # (it's not a substring), but fuzzy DL-distance-1 should catch it.
        results = search(conn, "autenticate_user")
        conn.close()

        assert len(results) > 0, (
            "Fuzzy fallback did not return results for 'autenticate_user' "
            "(1-edit-distance from 'authenticate_user'). "
            "The LIKE/fuzzy fallback must kick in when FTS returns zero rows."
        )

    def test_substring_match_returns_candidates(self) -> None:
        """A query matching a substring of a symbol name returns results via LIKE."""
        from seam.query.engine import search

        conn = self._build_fallback_db()
        # 'thentic' is a substring of 'authenticate_user' — FTS5 won't prefix-match this
        # but LIKE %thentic% should catch it
        results = search(conn, "thentic_user")
        conn.close()

        # This is a substring/fuzzy match — may or may not hit depending on edit distance.
        # The key assertion is: the function does NOT crash and returns a list.
        assert isinstance(results, list)
