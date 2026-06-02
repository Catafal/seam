"""Unit tests for Phase 5 additions to seam/analysis/confidence.py.

TDD: tests written BEFORE implementation (RED phase).

Test groups:
    R1 — resolve_edge() returns Resolution TypedDict with confidence + resolved_by.
    R2 — resolved_by = 'name-unique' when count == 1.
    R3 — resolved_by = 'name-collision' when count > 1.
    R4 — resolved_by = 'builtin' when count == 0 AND name is a builtin (language provided).
    R5 — resolved_by = 'unresolved' when count == 0 AND name is NOT a builtin.
    R6 — User story 5: user def get() (count >= 1) is NEVER 'builtin' resolved.
    R7 — resolve() shim still returns bare string (backward compat, story 26).
    R8 — best_candidate is None when no proximity candidates provided.
    R9 — best_candidate is set for AMBIGUOUS when candidate paths provided.
    R10 — Builtin check fires ONLY at count==0 (structural correctness guarantee).
    R11 — resolve_edge() never raises on bad/missing inputs.
    R12 — Without language, no builtin promotion (defaults to unresolved).
"""


from seam.analysis.confidence import (
    CONFIDENCE_AMBIGUOUS,
    CONFIDENCE_EXTRACTED,
    CONFIDENCE_INFERRED,
    resolve,
    resolve_edge,
)


class TestResolveEdgeBasicShape:
    """R1 — resolve_edge() returns a Resolution TypedDict."""

    def test_returns_resolution_typeddict(self) -> None:
        result = resolve_edge("foo", {"foo": 1})
        assert isinstance(result, dict)
        assert "confidence" in result
        assert "resolved_by" in result
        assert "best_candidate" in result

    def test_confidence_field_is_string(self) -> None:
        result = resolve_edge("foo", {"foo": 1})
        assert isinstance(result["confidence"], str)

    def test_resolved_by_is_string_or_none(self) -> None:
        result = resolve_edge("foo", {"foo": 1})
        assert result["resolved_by"] is None or isinstance(result["resolved_by"], str)


class TestNameUnique:
    """R2 — count == 1 → EXTRACTED, resolved_by = 'name-unique'."""

    def test_name_unique_confidence(self) -> None:
        result = resolve_edge("parse", {"parse": 1})
        assert result["confidence"] == CONFIDENCE_EXTRACTED

    def test_name_unique_resolved_by(self) -> None:
        result = resolve_edge("parse", {"parse": 1})
        assert result["resolved_by"] == "name-unique"

    def test_name_unique_best_candidate_none(self) -> None:
        result = resolve_edge("parse", {"parse": 1})
        assert result["best_candidate"] is None


class TestNameCollision:
    """R3 — count > 1 → AMBIGUOUS, resolved_by = 'name-collision'."""

    def test_collision_confidence(self) -> None:
        result = resolve_edge("parse", {"parse": 3})
        assert result["confidence"] == CONFIDENCE_AMBIGUOUS

    def test_collision_resolved_by(self) -> None:
        result = resolve_edge("parse", {"parse": 3})
        assert result["resolved_by"] == "name-collision"

    def test_two_copies_is_collision(self) -> None:
        result = resolve_edge("helper", {"helper": 2})
        assert result["confidence"] == CONFIDENCE_AMBIGUOUS
        assert result["resolved_by"] == "name-collision"


class TestBuiltinFiltering:
    """R4 — count == 0 AND is_builtin → INFERRED, resolved_by = 'builtin'."""

    def test_python_builtin_len(self) -> None:
        result = resolve_edge("len", {}, language="python")
        assert result["confidence"] == CONFIDENCE_INFERRED
        assert result["resolved_by"] == "builtin"

    def test_python_builtin_print(self) -> None:
        result = resolve_edge("print", {}, language="python")
        assert result["resolved_by"] == "builtin"

    def test_go_builtin_make(self) -> None:
        result = resolve_edge("make", {}, language="go")
        assert result["confidence"] == CONFIDENCE_INFERRED
        assert result["resolved_by"] == "builtin"

    def test_rust_builtin_vec_new(self) -> None:
        # Vec is in the Rust prelude
        result = resolve_edge("Vec", {}, language="rust")
        assert result["resolved_by"] == "builtin"

    def test_typescript_builtin_console(self) -> None:
        result = resolve_edge("console", {}, language="typescript")
        assert result["resolved_by"] == "builtin"

    def test_js_builtin_set_timeout(self) -> None:
        result = resolve_edge("setTimeout", {}, language="javascript")
        assert result["resolved_by"] == "builtin"

    def test_builtin_best_candidate_is_none(self) -> None:
        result = resolve_edge("len", {}, language="python")
        assert result["best_candidate"] is None


class TestUnresolved:
    """R5 — count == 0 AND NOT a builtin → INFERRED, resolved_by = 'unresolved'."""

    def test_unresolved_user_symbol(self) -> None:
        result = resolve_edge("my_external_lib", {}, language="python")
        assert result["confidence"] == CONFIDENCE_INFERRED
        assert result["resolved_by"] == "unresolved"

    def test_unresolved_best_candidate_none(self) -> None:
        result = resolve_edge("mystery_func", {}, language="python")
        assert result["best_candidate"] is None

    def test_unresolved_without_language(self) -> None:
        # Without language, no builtin check → unresolved
        result = resolve_edge("len", {})
        assert result["confidence"] == CONFIDENCE_INFERRED
        assert result["resolved_by"] == "unresolved"


class TestUserStory5CorrectnesGuarantee:
    """R6 — A user-defined name (count >= 1) is NEVER treated as builtin.

    This is the most critical test: user story 5 requires that structural
    ordering (count==0 guard) ensures this, not just a set-membership check.
    """

    def test_user_defined_get_not_filtered(self) -> None:
        # 'get' is NOT in the Python builtin set, but this test verifies
        # the count==1 path is taken before ANY builtin check.
        result = resolve_edge("get", {"get": 1}, language="python")
        assert result["confidence"] == CONFIDENCE_EXTRACTED
        assert result["resolved_by"] == "name-unique"

    def test_user_defined_len_not_filtered(self) -> None:
        # 'len' IS in the Python builtin set — but since count == 1,
        # the builtin check must NOT fire. This is the critical structural test.
        result = resolve_edge("len", {"len": 1}, language="python")
        assert result["confidence"] == CONFIDENCE_EXTRACTED
        assert result["resolved_by"] == "name-unique"  # NOT 'builtin'

    def test_user_defined_print_collision_not_filtered(self) -> None:
        # 'print' is a Python builtin, but with count > 1 we get collision,
        # NEVER 'builtin' since the count > 0 guard prevents it.
        result = resolve_edge("print", {"print": 2}, language="python")
        assert result["confidence"] == CONFIDENCE_AMBIGUOUS
        assert result["resolved_by"] == "name-collision"  # NOT 'builtin'

    def test_user_defined_make_in_go_not_filtered(self) -> None:
        # 'make' IS a Go builtin, but count == 1 → name-unique, not builtin
        result = resolve_edge("make", {"make": 1}, language="go")
        assert result["confidence"] == CONFIDENCE_EXTRACTED
        assert result["resolved_by"] == "name-unique"


class TestBackwardCompatShim:
    """R7 — resolve() still returns a bare string (backward compat, story 26)."""

    def test_shim_returns_string_extracted(self) -> None:
        result = resolve("foo", {"foo": 1})
        assert result == CONFIDENCE_EXTRACTED
        assert isinstance(result, str)

    def test_shim_returns_string_ambiguous(self) -> None:
        result = resolve("foo", {"foo": 3})
        assert result == CONFIDENCE_AMBIGUOUS

    def test_shim_returns_string_inferred(self) -> None:
        result = resolve("unknown_thing", {})
        assert result == CONFIDENCE_INFERRED

    def test_shim_not_a_resolution_dict(self) -> None:
        result = resolve("foo", {"foo": 1})
        assert not isinstance(result, dict)


class TestBestCandidateProximity:
    """R8/R9 — best_candidate on AMBIGUOUS when candidate_files provided."""

    def test_no_candidates_best_candidate_none(self) -> None:
        result = resolve_edge("parse", {"parse": 3})
        assert result["best_candidate"] is None

    def test_single_candidate_becomes_best(self) -> None:
        from pathlib import Path
        result = resolve_edge(
            "parse",
            {"parse": 3},
            referencing_file=Path("/project/app/parser.py"),
            candidate_files=["/project/app/json_parser.py"],
        )
        assert result["best_candidate"] == "/project/app/json_parser.py"

    def test_closer_file_wins_proximity(self) -> None:
        from pathlib import Path
        # referencing_file is in /project/app/ — the candidate in same dir should win
        result = resolve_edge(
            "parse",
            {"parse": 3},
            referencing_file=Path("/project/app/router.py"),
            candidate_files=[
                "/project/lib/parser.py",      # 1 shared segment
                "/project/app/parser.py",      # 2 shared segments (same dir)
            ],
        )
        assert result["best_candidate"] == "/project/app/parser.py"

    def test_best_candidate_none_for_non_ambiguous(self) -> None:
        from pathlib import Path
        # For EXTRACTED or INFERRED, best_candidate is None even with candidates
        result = resolve_edge(
            "parse",
            {"parse": 1},
            referencing_file=Path("/project/app/router.py"),
            candidate_files=["/project/app/parser.py"],
        )
        assert result["best_candidate"] is None


class TestStructuralCountZeroGuarantee:
    """R10 — Builtin check fires ONLY at count==0. Anything >0 skips it entirely."""

    def test_count_zero_fires_builtin(self) -> None:
        result = resolve_edge("len", {"other_name": 1}, language="python")
        # "len" not in name_counts → count == 0 → builtin check fires
        assert result["resolved_by"] == "builtin"

    def test_count_one_skips_builtin_entirely(self) -> None:
        result = resolve_edge("len", {"len": 1}, language="python")
        # count == 1 → name-unique path taken before builtin check
        assert result["resolved_by"] == "name-unique"

    def test_count_two_skips_builtin_entirely(self) -> None:
        result = resolve_edge("len", {"len": 2}, language="python")
        # count == 2 → name-collision path taken before builtin check
        assert result["resolved_by"] == "name-collision"


class TestNeverRaises:
    """R11 — resolve_edge() never raises on bad inputs."""

    def test_empty_name(self) -> None:
        # Should not raise
        result = resolve_edge("", {})
        assert "confidence" in result

    def test_none_language(self) -> None:
        result = resolve_edge("foo", {}, language=None)
        assert "confidence" in result

    def test_empty_name_counts(self) -> None:
        result = resolve_edge("foo", {})
        assert result["confidence"] == CONFIDENCE_INFERRED

    def test_unknown_language(self) -> None:
        result = resolve_edge("len", {}, language="brainfuck")
        # Unknown language → no builtin check → unresolved
        assert result["resolved_by"] == "unresolved"
