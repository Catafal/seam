"""Unit tests for WS1-A body-slice embedding enrichment (TDD — RED first).

Tests the two new pure helpers:
  - symbol_text() body budgeting (keyword-only body + max_chars args)
  - extract_body_slice() range extraction from pre-read lines

All tests are GATE-SAFE: no disk IO, no fastembed, no model download.

Test groups:
    B1 — symbol_text() budgeting: header-never-truncated, body fill,
         over-budget truncation, byte-identical 3-arg default, None/empty omission.
    B2 — extract_body_slice(): normal range, out-of-range start/end,
         single-line, empty input, start > end.
"""

import pytest

# ── B1: symbol_text() body budgeting ─────────────────────────────────────────


class TestSymbolTextBodyBudgeting:
    """B1 — symbol_text with body + max_chars keyword args."""

    def test_three_arg_call_unchanged(self) -> None:
        """Calling symbol_text with only the original 3 args is byte-identical to before."""
        from seam.analysis.embeddings import symbol_text

        result = symbol_text("my_fn", "def my_fn(x: int) -> str", "Converts x.")
        # Must match exactly the pre-feature output (newline-joined non-empty parts)
        assert result == "my_fn\ndef my_fn(x: int) -> str\nConverts x."

    def test_three_arg_no_sig_no_doc_unchanged(self) -> None:
        """3-arg call with None sig/doc is byte-identical to before."""
        from seam.analysis.embeddings import symbol_text

        result = symbol_text("orphan", None, None)
        assert result == "orphan"

    def test_body_appended_when_budget_remains(self) -> None:
        """Body is appended (newline-separated) when max_chars budget allows it."""
        from seam.analysis.embeddings import symbol_text

        header = "tiny_fn"
        body = "    return 42"
        result = symbol_text("tiny_fn", None, None, body=body, max_chars=200)
        assert result.startswith(header)
        assert body in result
        # Separated by a newline
        assert result == f"{header}\n{body}"

    def test_body_not_included_when_no_max_chars(self) -> None:
        """body without max_chars is ignored (same as body=None)."""
        from seam.analysis.embeddings import symbol_text

        result = symbol_text("fn", "def fn()", "doc", body="    x = 1")
        # No body in output — max_chars not set so body path inactive
        assert "x = 1" not in result

    def test_header_never_truncated_even_when_over_budget(self) -> None:
        """Header (name+sig+doc) is NEVER truncated, even if it exceeds max_chars."""
        from seam.analysis.embeddings import symbol_text

        long_sig = "def my_function(a: int, b: str, c: float, d: bool, e: list) -> dict"
        long_doc = "This function does something very important with many parameters."
        # Tiny max_chars so header alone exceeds it
        result = symbol_text("my_function", long_sig, long_doc, body="    pass", max_chars=10)

        # Header must appear in full
        assert "my_function" in result
        assert long_sig in result
        assert long_doc in result
        # Body must NOT be appended (no budget left or went over)
        assert "pass" not in result

    def test_body_truncated_to_fill_remaining_budget(self) -> None:
        """Body is truncated to the remaining char budget after the header."""
        from seam.analysis.embeddings import symbol_text

        # Header is "fn" (2 chars)
        # Separator "\n" (1 char) → 3 chars used before body
        # max_chars=10 → remaining budget = 7 chars
        long_body = "1234567890EXTRA"
        result = symbol_text("fn", None, None, body=long_body, max_chars=10)

        assert result.startswith("fn\n")
        body_part = result[3:]  # after "fn\n"
        assert len(body_part) <= 7
        # Must be the LEADING slice of the body
        assert long_body.startswith(body_part)

    def test_body_shorter_than_budget_included_in_full(self) -> None:
        """A body shorter than the remaining budget is included without truncation."""
        from seam.analysis.embeddings import symbol_text

        body = "    return True"
        result = symbol_text("fn", None, None, body=body, max_chars=500)
        assert body in result

    def test_none_body_with_max_chars_no_error(self) -> None:
        """body=None with max_chars set is fine — no body appended, no error."""
        from seam.analysis.embeddings import symbol_text

        result = symbol_text("fn", "def fn()", "doc", body=None, max_chars=500)
        assert result == "fn\ndef fn()\ndoc"

    def test_empty_body_with_max_chars_no_error(self) -> None:
        """body='' with max_chars set is fine — treated as no body, no empty line."""
        from seam.analysis.embeddings import symbol_text

        result = symbol_text("fn", None, None, body="", max_chars=500)
        assert result == "fn"

    def test_max_chars_zero_no_body(self) -> None:
        """max_chars=0 means no budget for body even if header fits in 0 chars."""
        from seam.analysis.embeddings import symbol_text

        # Header will exceed 0 chars, so body won't be appended
        result = symbol_text("fn", None, None, body="    pass", max_chars=0)
        assert "pass" not in result

    def test_symbol_text_returns_str_with_body(self) -> None:
        """Return type is always str, even with body path active."""
        from seam.analysis.embeddings import symbol_text

        result = symbol_text("fn", "def fn()", "doc", body="    pass", max_chars=200)
        assert isinstance(result, str)

    def test_body_with_all_header_fields(self) -> None:
        """Header with name+sig+doc assembled first, then body appended."""
        from seam.analysis.embeddings import symbol_text

        result = symbol_text(
            "compute",
            "def compute(x: int) -> int",
            "Returns x*2.",
            body="    return x * 2",
            max_chars=500,
        )
        # Header fields all present in order
        lines = result.splitlines()
        assert lines[0] == "compute"
        assert lines[1] == "def compute(x: int) -> int"
        assert lines[2] == "Returns x*2."
        assert lines[3] == "    return x * 2"


# ── B2: extract_body_slice() edge cases ──────────────────────────────────────


class TestExtractBodySlice:
    """B2 — extract_body_slice handles all edge cases without raising."""

    def test_normal_range_returns_joined_lines(self) -> None:
        """Normal 1-based inclusive range returns correct joined text."""
        from seam.analysis.embeddings import extract_body_slice

        lines = ["def foo():", "    x = 1", "    return x", "# comment"]
        result = extract_body_slice(lines, 1, 3)
        assert result == "def foo():\n    x = 1\n    return x"

    def test_single_line_returns_that_line(self) -> None:
        """When start_line == end_line, returns that single line."""
        from seam.analysis.embeddings import extract_body_slice

        lines = ["line1", "line2", "line3"]
        result = extract_body_slice(lines, 2, 2)
        assert result == "line2"

    def test_empty_source_lines_returns_empty_string(self) -> None:
        """Empty source_lines list returns '' without raising."""
        from seam.analysis.embeddings import extract_body_slice

        result = extract_body_slice([], 1, 5)
        assert result == ""

    def test_start_greater_than_end_returns_empty_string(self) -> None:
        """start_line > end_line returns '' without raising."""
        from seam.analysis.embeddings import extract_body_slice

        lines = ["a", "b", "c"]
        result = extract_body_slice(lines, 3, 1)
        assert result == ""

    def test_start_zero_returns_empty_string(self) -> None:
        """start_line=0 (out-of-range for 1-based) returns '' without raising."""
        from seam.analysis.embeddings import extract_body_slice

        lines = ["a", "b"]
        result = extract_body_slice(lines, 0, 1)
        assert result == ""

    def test_start_negative_returns_empty_string(self) -> None:
        """Negative start_line returns '' without raising."""
        from seam.analysis.embeddings import extract_body_slice

        lines = ["a", "b"]
        result = extract_body_slice(lines, -1, 1)
        assert result == ""

    def test_end_beyond_list_clamps_gracefully(self) -> None:
        """end_line beyond len(source_lines) clamps to last line without raising."""
        from seam.analysis.embeddings import extract_body_slice

        lines = ["a", "b", "c"]
        result = extract_body_slice(lines, 2, 99)
        # Should return lines 2 and 3 (b and c), clamped
        assert result == "b\nc"

    def test_start_beyond_list_returns_empty_string(self) -> None:
        """start_line > len(source_lines) returns '' without raising."""
        from seam.analysis.embeddings import extract_body_slice

        lines = ["a", "b"]
        result = extract_body_slice(lines, 10, 15)
        assert result == ""

    def test_returns_str_always(self) -> None:
        """Return type is always str, never raises."""
        from seam.analysis.embeddings import extract_body_slice

        assert isinstance(extract_body_slice([], 1, 1), str)
        assert isinstance(extract_body_slice(["x"], 1, 1), str)
        assert isinstance(extract_body_slice(["x"], 5, 1), str)

    def test_never_raises_on_pathological_inputs(self) -> None:
        """extract_body_slice never raises for any combination of inputs."""
        from seam.analysis.embeddings import extract_body_slice

        bad_cases = [
            ([], 0, 0),
            ([], -1, -1),
            (["a"], 0, 0),
            (["a"], 2, 1),
            (["a", "b"], 999, 1000),
        ]
        for lines, start, end in bad_cases:
            try:
                extract_body_slice(lines, start, end)
            except Exception as exc:  # noqa: BLE001
                pytest.fail(
                    f"extract_body_slice({lines!r}, {start}, {end}) raised: {exc}"
                )

    def test_full_file_range(self) -> None:
        """Requesting the full file (1 to len) returns all lines joined."""
        from seam.analysis.embeddings import extract_body_slice

        lines = ["class Foo:", "    def bar(self):", "        pass"]
        result = extract_body_slice(lines, 1, len(lines))
        assert result == "class Foo:\n    def bar(self):\n        pass"
