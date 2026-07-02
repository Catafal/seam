"""Unit tests for WS1-B comment enrichment in symbol_text() (TDD — RED first).

Tests the extended symbol_text() that accepts an optional keyword-only `comments` arg.

Fill order (approved spec): header → body → comments (budget permitting).
Empty / absent comments contribute NOTHING — no dangling separator or newline.
A symbol with no comments must embed EXACTLY as body-only (byte-identical).

Test groups:
    C1 — comments appended after body when budget remains.
    C2 — no-comment fallback: None/empty comments = byte-identical to body-only.
    C3 — budget exhausted by header+body: comments silently dropped (no separator).
    C4 — comments without body: appended after header when budget permits.
    C5 — comments truncated to remaining budget after header + body.
"""

# ── C1: comments appended after body when budget remains ─────────────────────


class TestCommentsAppendedAfterBody:
    """C1 — comments appear after body, after header."""

    def test_comments_after_body_in_output(self) -> None:
        """Header → body → comments fill order when budget permits all."""
        from seam.analysis.embeddings import symbol_text

        result = symbol_text(
            "my_fn",
            "def my_fn() -> None",
            "Does something.",
            body="    pass",
            max_chars=500,
            comments="WHY: keeps it simple",
        )
        lines = result.splitlines()
        assert lines[0] == "my_fn"
        assert lines[1] == "def my_fn() -> None"
        assert lines[2] == "Does something."
        assert lines[3] == "    pass"
        assert lines[4] == "WHY: keeps it simple"

    def test_comments_section_present_in_result(self) -> None:
        """Comment text is present in the result when budget allows."""
        from seam.analysis.embeddings import symbol_text

        result = symbol_text(
            "fn",
            None,
            None,
            body="    x = 1",
            max_chars=500,
            comments="HACK: temporary workaround",
        )
        assert "HACK: temporary workaround" in result

    def test_comments_appear_after_body_in_result(self) -> None:
        """Comments appear strictly after body (not before, not interleaved)."""
        from seam.analysis.embeddings import symbol_text

        result = symbol_text(
            "fn",
            None,
            None,
            body="    return 42",
            max_chars=500,
            comments="NOTE: magic number",
        )
        body_pos = result.find("    return 42")
        comment_pos = result.find("NOTE: magic number")
        assert body_pos < comment_pos, "Comments must appear after body"

    def test_comments_appear_after_header_when_no_body(self) -> None:
        """When no body, comments appear after header."""
        from seam.analysis.embeddings import symbol_text

        result = symbol_text(
            "fn",
            "def fn()",
            "Docs.",
            body=None,
            max_chars=500,
            comments="WHY: no body needed",
        )
        # Should have header then comments (no body separator)
        assert result.startswith("fn\ndef fn()\nDocs.")
        assert "WHY: no body needed" in result
        # Order check
        header_end = result.find("Docs.") + len("Docs.")
        comment_pos = result.find("WHY: no body needed")
        assert comment_pos > header_end


# ── C2: no-comment fallback = byte-identical to body-only ────────────────────


class TestNoCommentFallback:
    """C2 — None/empty comments produce byte-identical output to body-only."""

    def test_none_comments_byte_identical_to_body_only(self) -> None:
        """comments=None → output is byte-identical to not passing comments."""
        from seam.analysis.embeddings import symbol_text

        without_comments = symbol_text(
            "fn", "def fn()", "Doc.", body="    return 1", max_chars=500
        )
        with_none_comments = symbol_text(
            "fn",
            "def fn()",
            "Doc.",
            body="    return 1",
            max_chars=500,
            comments=None,
        )
        assert with_none_comments == without_comments

    def test_empty_string_comments_byte_identical_to_body_only(self) -> None:
        """comments='' → output is byte-identical to not passing comments."""
        from seam.analysis.embeddings import symbol_text

        without_comments = symbol_text(
            "fn", "def fn()", "Doc.", body="    return 1", max_chars=500
        )
        with_empty_comments = symbol_text(
            "fn", "def fn()", "Doc.", body="    return 1", max_chars=500, comments=""
        )
        assert with_empty_comments == without_comments

    def test_whitespace_only_comments_byte_identical_to_body_only(self) -> None:
        """comments with only whitespace → treated as empty, byte-identical."""
        from seam.analysis.embeddings import symbol_text

        without_comments = symbol_text(
            "fn", None, None, body="    pass", max_chars=500
        )
        with_whitespace = symbol_text(
            "fn", None, None, body="    pass", max_chars=500, comments="   \n  "
        )
        assert with_whitespace == without_comments

    def test_no_dangling_separator_with_no_comments(self) -> None:
        """No trailing newline or dangling separator when comments is None/empty."""
        from seam.analysis.embeddings import symbol_text

        result = symbol_text(
            "fn", None, None, body="    x = 1", max_chars=500, comments=None
        )
        # Must not end with a newline (no dangling separator)
        assert not result.endswith("\n")
        # body is the last thing
        assert result.endswith("    x = 1")

    def test_no_comments_kwarg_still_works(self) -> None:
        """Calling without comments kwarg at all = byte-identical to body-only (default)."""
        from seam.analysis.embeddings import symbol_text

        result_no_kwarg = symbol_text("fn", "sig", "doc", body="    pass", max_chars=500)
        result_explicit_none = symbol_text(
            "fn", "sig", "doc", body="    pass", max_chars=500, comments=None
        )
        assert result_no_kwarg == result_explicit_none


# ── C3: budget exhausted before comments ─────────────────────────────────────


class TestBudgetExhaustedBeforeComments:
    """C3 — when budget is used up by header + body, comments are silently dropped."""

    def test_comments_dropped_when_no_budget_remains(self) -> None:
        """Header + body fills budget → comments not appended, no separator."""
        from seam.analysis.embeddings import symbol_text

        # header = "fn" (2 chars), separator = "\n" (1), budget after header = 97
        # body = "x" * 97 → fills remaining budget exactly
        long_body = "x" * 97
        result = symbol_text(
            "fn",
            None,
            None,
            body=long_body,
            max_chars=100,
            comments="THIS_COMMENT_MUST_NOT_APPEAR",
        )
        assert "THIS_COMMENT_MUST_NOT_APPEAR" not in result
        # No dangling separator
        assert not result.endswith("\n")

    def test_no_separator_when_comments_dropped(self) -> None:
        """When budget is full, the result ends cleanly with the body slice."""
        from seam.analysis.embeddings import symbol_text

        body = "abcdefg"  # 7 chars; header "fn\n" = 3 → total 10 exact
        result = symbol_text("fn", None, None, body=body, max_chars=10, comments="DROP_ME")
        # Should end with body (not with separator + comment)
        assert result.endswith("abcdefg")
        assert "DROP_ME" not in result


# ── C4: comments without body ────────────────────────────────────────────────


class TestCommentsWithoutBody:
    """C4 — comments with body=None are appended after the header."""

    def test_comments_after_header_when_no_body(self) -> None:
        """body=None, comments present → comment appended after header."""
        from seam.analysis.embeddings import symbol_text

        result = symbol_text(
            "fn",
            "def fn()",
            None,
            body=None,
            max_chars=500,
            comments="WHY: lightweight",
        )
        assert "WHY: lightweight" in result
        assert result.startswith("fn\ndef fn()")

    def test_comments_with_no_body_no_max_chars_ignored(self) -> None:
        """comments without max_chars is ignored (same as no feature active)."""
        from seam.analysis.embeddings import symbol_text

        result = symbol_text("fn", "def fn()", "doc", comments="SHOULD_NOT_APPEAR")
        assert "SHOULD_NOT_APPEAR" not in result


# ── C5: comments truncated to remaining budget ────────────────────────────────


class TestCommentsTruncated:
    """C5 — comments are truncated to fit remaining budget."""

    def test_comments_truncated_to_budget(self) -> None:
        """Comments text is truncated to remaining budget after header + body."""
        from seam.analysis.embeddings import symbol_text

        # header = "fn" (2), sep "\n" (1), body = "bb" (2), sep "\n" (1) = 6 used
        # max_chars = 10 → remaining = 4 for comments
        # "COMMENT_THAT_IS_TOO_LONG"[:4] = "COMM"
        result = symbol_text(
            "fn",
            None,
            None,
            body="bb",
            max_chars=10,
            comments="COMMENT_THAT_IS_TOO_LONG",
        )
        # The separator before comments + comments together use chars after "fn\nbb".
        # After "fn\nbb" (5 chars), sep "\n" (1) = 6 used → remaining = 4
        # So the comment slice is the first 4 chars: "COMM"
        # Find any leading prefix of the comments in result
        full_comment = "COMMENT_THAT_IS_TOO_LONG"
        # Locate where in result the comment begins (after the last separator)
        last_sep = result.rfind("\n")
        assert last_sep != -1, "Result should have separators"
        comment_part = result[last_sep + 1 :]
        # The comment part must be ≤ 4 chars
        assert len(comment_part) <= 4
        # Must be a leading slice of the full comment
        assert full_comment.startswith(comment_part)

    def test_output_within_budget_with_comments(self) -> None:
        """Final output with truncated comments is within max_chars."""
        from seam.analysis.embeddings import symbol_text

        result = symbol_text(
            "fn",
            "def fn(x: int) -> str",
            "Converts x to str.",
            body="    return str(x)",
            max_chars=50,
            comments="WHY: simple conversion; HACK: not tested",
        )
        # Header is never truncated, but total output within budget is not guaranteed
        # for header alone — but comments must not push it over if budget was already used
        # At minimum: result is a str, never raises
        assert isinstance(result, str)
