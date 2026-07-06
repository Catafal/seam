"""Unit tests for seam/eval/trace_derive.py — derive_outcome_from_diff().

Tests use synthetic unified diffs and in-memory symbol index rows.
All tests are offline — no DB, no network, no model download.

Coverage:
  OS1 — Basic: a hunk touching a symbol's line range → symbol in result.
  OS2 — Empty diff → empty set.
  OS3 — Hunk touching NO indexed symbol → empty set (no error).
  OS4 — Multi-symbol hunk: a hunk spanning multiple symbols → all matched.
  OS5 — Multi-file diff: symbols from different files resolved independently.
  OS6 — Symbol at exact hunk start/end (boundary condition).
  OS7 — Hunk outside all symbol ranges → empty set.
  OS8 — Non-Python unified diff (same logic, just file paths differ).
  OS9 — Pure: function accepts only a diff string + a list of index rows.
"""

from __future__ import annotations

# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_symbol(
    *,
    name: str,
    file_path: str,
    start_line: int,
    end_line: int,
) -> dict:
    """Build a minimal symbol index row (as derive_outcome_from_diff expects)."""
    return {
        "name": name,
        "file_path": file_path,
        "start_line": start_line,
        "end_line": end_line,
    }


def _make_diff(
    file_path: str,
    hunks: list[tuple[int, int]],
) -> str:
    """Build a minimal unified diff string for one file with the given hunks.

    Each hunk is (start_line, n_lines) in the NEW file (the side an agent edits).
    The diff format we generate is parseable by derive_outcome_from_diff.
    """
    lines = [
        f"diff --git a/{file_path} b/{file_path}",
        f"--- a/{file_path}",
        f"+++ b/{file_path}",
    ]
    for start, n in hunks:
        lines.append(f"@@ -{start},{n} +{start},{n} @@")
        for i in range(n):
            lines.append(f"+line {start + i}")
    return "\n".join(lines) + "\n"


# ── OS1: Basic ────────────────────────────────────────────────────────────────


class TestBasicOutcome:
    def test_hunk_in_range_returns_symbol(self) -> None:
        from seam.eval.trace_derive import derive_outcome_from_diff  # noqa: PLC0415

        diff = _make_diff("seam/foo.py", [(5, 3)])  # lines 5-7
        symbols = [_make_symbol(name="Foo.bar", file_path="seam/foo.py", start_line=3, end_line=10)]
        result = derive_outcome_from_diff(diff, symbols)
        assert "Foo.bar" in result


# ── OS2: Empty diff ────────────────────────────────────────────────────────────


class TestEmptyDiff:
    def test_empty_string_returns_empty_set(self) -> None:
        from seam.eval.trace_derive import derive_outcome_from_diff  # noqa: PLC0415

        symbols = [_make_symbol(name="Foo", file_path="foo.py", start_line=1, end_line=5)]
        result = derive_outcome_from_diff("", symbols)
        assert result == set()

    def test_empty_symbols_returns_empty_set(self) -> None:
        from seam.eval.trace_derive import derive_outcome_from_diff  # noqa: PLC0415

        diff = _make_diff("foo.py", [(1, 3)])
        result = derive_outcome_from_diff(diff, [])
        assert result == set()


# ── OS3: Hunk touching no indexed symbol ──────────────────────────────────────


class TestHunkNoSymbol:
    def test_hunk_in_different_file_returns_empty(self) -> None:
        from seam.eval.trace_derive import derive_outcome_from_diff  # noqa: PLC0415

        diff = _make_diff("other/file.py", [(1, 3)])
        symbols = [_make_symbol(name="Foo", file_path="my/file.py", start_line=1, end_line=10)]
        result = derive_outcome_from_diff(diff, symbols)
        assert result == set()

    def test_hunk_outside_symbol_range_returns_empty(self) -> None:
        from seam.eval.trace_derive import derive_outcome_from_diff  # noqa: PLC0415

        # Symbol is at lines 20-30, hunk is at lines 1-5
        diff = _make_diff("foo.py", [(1, 5)])
        symbols = [_make_symbol(name="Foo", file_path="foo.py", start_line=20, end_line=30)]
        result = derive_outcome_from_diff(diff, symbols)
        assert result == set()


# ── OS4: Multi-symbol hunk ────────────────────────────────────────────────────


class TestMultiSymbolHunk:
    def test_hunk_spanning_two_symbols_returns_both(self) -> None:
        from seam.eval.trace_derive import derive_outcome_from_diff  # noqa: PLC0415

        # Hunk at lines 5-15 spans both symbols
        diff = _make_diff("foo.py", [(5, 11)])
        symbols = [
            _make_symbol(name="Foo.method1", file_path="foo.py", start_line=3, end_line=8),
            _make_symbol(name="Foo.method2", file_path="foo.py", start_line=9, end_line=15),
        ]
        result = derive_outcome_from_diff(diff, symbols)
        assert "Foo.method1" in result
        assert "Foo.method2" in result


# ── OS5: Multi-file diff ──────────────────────────────────────────────────────


class TestMultiFileDiff:
    def test_symbols_resolved_per_file(self) -> None:
        from seam.eval.trace_derive import derive_outcome_from_diff  # noqa: PLC0415

        diff = (
            _make_diff("a.py", [(1, 3)])
            + _make_diff("b.py", [(1, 3)])
        )
        symbols = [
            _make_symbol(name="A.foo", file_path="a.py", start_line=1, end_line=5),
            _make_symbol(name="B.bar", file_path="b.py", start_line=1, end_line=5),
        ]
        result = derive_outcome_from_diff(diff, symbols)
        assert "A.foo" in result
        assert "B.bar" in result


# ── OS6: Boundary conditions ──────────────────────────────────────────────────


class TestBoundaryConditions:
    def test_hunk_at_exact_symbol_start(self) -> None:
        from seam.eval.trace_derive import derive_outcome_from_diff  # noqa: PLC0415

        # Hunk starts at the SAME line as the symbol
        diff = _make_diff("foo.py", [(5, 1)])
        symbols = [_make_symbol(name="Foo", file_path="foo.py", start_line=5, end_line=10)]
        result = derive_outcome_from_diff(diff, symbols)
        assert "Foo" in result

    def test_hunk_at_exact_symbol_end(self) -> None:
        from seam.eval.trace_derive import derive_outcome_from_diff  # noqa: PLC0415

        # Hunk is at the last line of the symbol
        diff = _make_diff("foo.py", [(10, 1)])
        symbols = [_make_symbol(name="Foo", file_path="foo.py", start_line=5, end_line=10)]
        result = derive_outcome_from_diff(diff, symbols)
        assert "Foo" in result

    def test_hunk_just_before_symbol_returns_empty(self) -> None:
        from seam.eval.trace_derive import derive_outcome_from_diff  # noqa: PLC0415

        # Hunk is at lines 1-4, symbol starts at 5
        diff = _make_diff("foo.py", [(1, 4)])
        symbols = [_make_symbol(name="Foo", file_path="foo.py", start_line=5, end_line=10)]
        result = derive_outcome_from_diff(diff, symbols)
        assert "Foo" not in result

    def test_hunk_just_after_symbol_returns_empty(self) -> None:
        from seam.eval.trace_derive import derive_outcome_from_diff  # noqa: PLC0415

        # Hunk is at lines 11+, symbol ends at 10
        diff = _make_diff("foo.py", [(11, 3)])
        symbols = [_make_symbol(name="Foo", file_path="foo.py", start_line=5, end_line=10)]
        result = derive_outcome_from_diff(diff, symbols)
        assert "Foo" not in result


# ── OS7: Hunk outside all ranges ─────────────────────────────────────────────


class TestHunkOutsideAllRanges:
    def test_multiple_symbols_none_touched(self) -> None:
        from seam.eval.trace_derive import derive_outcome_from_diff  # noqa: PLC0415

        diff = _make_diff("foo.py", [(100, 5)])
        symbols = [
            _make_symbol(name="A", file_path="foo.py", start_line=1, end_line=10),
            _make_symbol(name="B", file_path="foo.py", start_line=11, end_line=20),
        ]
        result = derive_outcome_from_diff(diff, symbols)
        assert result == set()


# ── OS8: Non-Python file ──────────────────────────────────────────────────────


class TestNonPythonFile:
    def test_typescript_file_resolved(self) -> None:
        from seam.eval.trace_derive import derive_outcome_from_diff  # noqa: PLC0415

        diff = _make_diff("src/auth.ts", [(10, 5)])
        symbols = [_make_symbol(name="AuthService.login", file_path="src/auth.ts", start_line=8, end_line=15)]
        result = derive_outcome_from_diff(diff, symbols)
        assert "AuthService.login" in result


# ── OS9: Pure function ────────────────────────────────────────────────────────


class TestPureOutcomeFunction:
    def test_does_not_mutate_symbols(self) -> None:
        from seam.eval.trace_derive import derive_outcome_from_diff  # noqa: PLC0415

        diff = _make_diff("foo.py", [(1, 3)])
        symbols = [_make_symbol(name="Foo", file_path="foo.py", start_line=1, end_line=5)]
        original = dict(symbols[0])
        derive_outcome_from_diff(diff, symbols)
        assert symbols[0] == original

    def test_returns_set(self) -> None:
        from seam.eval.trace_derive import derive_outcome_from_diff  # noqa: PLC0415

        result = derive_outcome_from_diff("", [])
        assert isinstance(result, set)

    def test_never_raises_on_malformed_diff(self) -> None:
        from seam.eval.trace_derive import derive_outcome_from_diff  # noqa: PLC0415

        # Should return empty set, not raise
        result = derive_outcome_from_diff("not a valid unified diff at all", [])
        assert isinstance(result, set)
