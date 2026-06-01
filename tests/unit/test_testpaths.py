"""Unit tests for seam/analysis/testpaths.py — is_test_file() heuristic.

Tests verify the EXTERNAL behavior of is_test_file: given a path string,
returns True for test files and False for non-test files. No internal detail.

TDD: these tests are written BEFORE the implementation so they drive it.

Positive patterns (must return True):
  P1  path with a 'tests' directory segment  (e.g. /proj/tests/foo.py)
  P2  path with a 'test' directory segment   (e.g. /proj/test/foo.py)
  P3  basename matches test_*                (e.g. test_widget.py)
  P4  basename matches *_test.py             (e.g. widget_test.py)
  P5  basename is conftest.py                (case-insensitive check: CONFTEST.PY too)
  P6  basename matches *.spec.ts
  P7  basename matches *.spec.tsx
  P8  basename matches *.spec.js
  P9  basename matches *.spec.jsx
  P10 basename matches *.test.ts
  P11 basename matches *.test.tsx
  P12 basename matches *.test.js
  P13 basename matches *.test.jsx
  P14 basename patterns are case-insensitive (Test_Widget.py)

False-positive negatives (must return False — the anchoring trap):
  N1  'latest.py'      — contains 'test' as substring of basename, NOT a segment or pattern
  N2  'attestation.py' — contains 'test' substring
  N3  'contest.py'     — contains 'test' substring
  N4  'production.py'  — plain prod file
  N5  None path        — must return False (not raise)
  N6  empty string     — must return False (not raise)
  N7  /some/contest/dir/foo.py — 'contest' is a dir segment, not exactly 'test' or 'tests'
  N8  /testdata/foo.py — 'testdata' dir segment must NOT match (only 'test' or 'tests' exactly)
"""

from seam.analysis.testpaths import is_test_file

# ── Positive: 'tests' directory segment ───────────────────────────────────────


def test_tests_dir_segment_unix() -> None:
    """A file under a 'tests/' directory must be detected as a test file."""
    assert is_test_file("/project/tests/test_widget.py") is True


def test_tests_dir_segment_nested() -> None:
    """A file under a nested 'tests/' segment must be detected as a test file."""
    assert is_test_file("/project/src/tests/unit/test_something.py") is True


# ── Positive: 'test' directory segment ────────────────────────────────────────


def test_test_dir_segment() -> None:
    """A file under a 'test/' directory must be detected as a test file."""
    assert is_test_file("/project/test/widget.py") is True


def test_test_dir_segment_nested() -> None:
    """A file under a nested 'test/' segment must be detected as a test file."""
    assert is_test_file("/project/src/test/integration/helper.py") is True


# ── Positive: basename test_* ─────────────────────────────────────────────────


def test_basename_test_prefix() -> None:
    """Basename matching 'test_*.py' must be detected as a test file."""
    assert is_test_file("/project/src/test_widget.py") is True


def test_basename_test_prefix_no_dir() -> None:
    """test_x.py with no directory component must still be detected."""
    assert is_test_file("test_widget.py") is True


# ── Positive: basename *_test.py ─────────────────────────────────────────────


def test_basename_test_suffix() -> None:
    """Basename matching '*_test.py' must be detected as a test file."""
    assert is_test_file("/project/src/widget_test.py") is True


# ── Positive: conftest.py ────────────────────────────────────────────────────


def test_conftest_exact() -> None:
    """conftest.py must be detected as a test file."""
    assert is_test_file("/project/conftest.py") is True


def test_conftest_case_insensitive() -> None:
    """CONFTEST.PY (upper-case) must also be detected (case-insensitive basename)."""
    assert is_test_file("/project/CONFTEST.PY") is True


# ── Positive: *.spec.{js,jsx,ts,tsx} ─────────────────────────────────────────


def test_spec_ts() -> None:
    """*.spec.ts must be detected as a test file."""
    assert is_test_file("/project/src/widget.spec.ts") is True


def test_spec_tsx() -> None:
    """*.spec.tsx must be detected as a test file."""
    assert is_test_file("/project/src/widget.spec.tsx") is True


def test_spec_js() -> None:
    """*.spec.js must be detected as a test file."""
    assert is_test_file("/project/src/widget.spec.js") is True


def test_spec_jsx() -> None:
    """*.spec.jsx must be detected as a test file."""
    assert is_test_file("/project/src/widget.spec.jsx") is True


# ── Positive: *.test.{js,jsx,ts,tsx} ─────────────────────────────────────────


def test_test_ts() -> None:
    """*.test.ts must be detected as a test file."""
    assert is_test_file("/project/src/widget.test.ts") is True


def test_test_tsx() -> None:
    """*.test.tsx must be detected as a test file."""
    assert is_test_file("/project/src/widget.test.tsx") is True


def test_test_js() -> None:
    """*.test.js must be detected as a test file."""
    assert is_test_file("/project/src/widget.test.js") is True


def test_test_jsx() -> None:
    """*.test.jsx must be detected as a test file."""
    assert is_test_file("/project/src/widget.test.jsx") is True


# ── Positive: case-insensitive basename patterns ──────────────────────────────


def test_basename_case_insensitive_prefix() -> None:
    """Test_Widget.py (mixed case) must be detected as a test file."""
    assert is_test_file("/project/Test_Widget.py") is True


def test_basename_case_insensitive_suffix() -> None:
    """Widget_Test.py (mixed case suffix) must be detected as a test file."""
    assert is_test_file("/project/Widget_Test.py") is True


# ── Negative: false-positive traps ────────────────────────────────────────────


def test_latest_py_not_test() -> None:
    """latest.py must NOT be detected as a test file (contains 'test' as substring)."""
    assert is_test_file("/project/src/latest.py") is False


def test_attestation_py_not_test() -> None:
    """attestation.py must NOT be detected as a test file."""
    assert is_test_file("/project/src/attestation.py") is False


def test_contest_py_not_test() -> None:
    """contest.py must NOT be detected as a test file."""
    assert is_test_file("/project/src/contest.py") is False


def test_production_py_not_test() -> None:
    """production.py is a plain source file; must not be detected."""
    assert is_test_file("/project/src/production.py") is False


def test_contest_dir_segment_not_test() -> None:
    """'contest' is a dir segment but NOT exactly 'test' or 'tests'; must not match."""
    assert is_test_file("/some/contest/dir/foo.py") is False


def test_testdata_dir_segment_not_test() -> None:
    """'testdata' dir segment must NOT match — only exact 'test' or 'tests' match."""
    assert is_test_file("/project/testdata/fixtures/sample.py") is False


def test_none_path_returns_false() -> None:
    """None path must return False without raising."""
    assert is_test_file(None) is False  # type: ignore[arg-type]


def test_empty_string_returns_false() -> None:
    """Empty string path must return False without raising."""
    assert is_test_file("") is False
