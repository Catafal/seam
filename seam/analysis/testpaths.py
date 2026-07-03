"""Test-file and package-plumbing path heuristics — pure functions, no I/O.

Used by:
  - impact.py: tag TieredEntry items with is_test (bool) via is_test_file
  - graph_api.py: exclude package barrels from ranked surfaces via is_package_file

Import hierarchy: this module imports nothing from seam (no circular deps).
It may safely be imported by any analysis-layer module.

is_test_file rule (documented):
    Returns True when the path satisfies ANY of:
      1. A directory SEGMENT (exact match, case-sensitive) is 'tests' or 'test'.
         Segment = one component from Path.parts — NOT a substring search.
         'testdata/', 'contest/', 'attest/' etc. do NOT match.
      2. The basename (case-insensitive) matches:
           test_*.py      — Python test_* prefix
           *_test.py      — Python *_test suffix
           conftest.py    — pytest config / fixtures
           *.spec.js      — JS/TS spec files
           *.spec.jsx
           *.spec.ts
           *.spec.tsx
           *.test.js
           *.test.jsx
           *.test.ts
           *.test.tsx

    Returns False for None or empty string (safe default).

is_package_file rule (documented):
    Returns True when the BASENAME (case-sensitive exact match) is one of:
      __init__.py   — Python package init
      __init__.pyi  — Python stub package init
      mod.rs        — Rust module root (package-plumbing convention)
      index.ts      — TypeScript barrel
      index.tsx     — TypeScript/JSX barrel
      index.js      — JavaScript barrel
      index.jsx     — JavaScript/JSX barrel

    Conservative: only exact basenames match (no substring or glob).
    Returns False for None or empty string (safe default).

False-positive protection for is_test_file:
    'latest.py', 'attestation.py', 'contest.py' must NOT match.
    Only Path.parts is used for directory checks — never substring search.
    Basename patterns are anchored (prefix/suffix) via str.startswith /
    str.endswith + explicit suffix checks.
"""

from pathlib import Path

# Directory segments that indicate a test tree.
# Case-sensitive: 'tests' and 'test' are conventional in Python projects.
_TEST_DIR_SEGMENTS: frozenset[str] = frozenset({"tests", "test"})

# JS/TS spec/test double-extensions.
# e.g. 'widget.spec.ts' — we check (name_lower ends with one of these).
_DOUBLE_EXTENSIONS: tuple[str, ...] = (
    ".spec.js",
    ".spec.jsx",
    ".spec.ts",
    ".spec.tsx",
    ".test.js",
    ".test.jsx",
    ".test.ts",
    ".test.tsx",
)


def is_test_file(path: str | None) -> bool:
    """Return True if path belongs to a test file; False otherwise.

    Args:
        path: Absolute or relative file path string, or None.

    Returns:
        True  — file is a test file (matches any rule above).
        False — file is a production file, unresolved, or path is None/empty.

    Never raises; None or empty string returns False.
    """
    # Guard: None or empty is treated as unknown → not a test file.
    if not path:
        return False

    p = Path(path)

    # Rule 1: check every directory segment (not the final basename) for exact match.
    # Path.parts gives ('/', 'project', 'tests', 'foo.py') for '/project/tests/foo.py'.
    # We skip the last part (the filename itself) — only directory segments matter here.
    directory_parts = p.parts[:-1]  # exclude the basename
    for part in directory_parts:
        if part in _TEST_DIR_SEGMENTS:
            return True

    # Rule 2: basename pattern matching (case-insensitive).
    name_lower = p.name.lower()

    # conftest.py — exact match on lowercased name.
    if name_lower == "conftest.py":
        return True

    # test_*.py — must start with 'test_' AND end with '.py'.
    # The 'test_' prefix is anchored at the start, preventing 'latest.py' matches.
    if name_lower.startswith("test_") and name_lower.endswith(".py"):
        return True

    # *_test.py — must end with '_test.py'.
    # Anchored suffix prevents 'attestation.py' matches (does not end with '_test.py').
    if name_lower.endswith("_test.py"):
        return True

    # *.spec.{js,jsx,ts,tsx} and *.test.{js,jsx,ts,tsx} — double-extension check.
    for ext in _DOUBLE_EXTENSIONS:
        if name_lower.endswith(ext):
            return True

    return False


# Exact basenames of package-plumbing entry-point files.
# WHY exact: 'my_index.ts' or 'lib.rs' are NOT barrels — only these conventional
# names are. Conservative: false-negatives (miss a non-standard barrel) are safe;
# false-positives (hide production code) are harmful.
_PACKAGE_BASENAMES: frozenset[str] = frozenset({
    "__init__.py",   # Python package init
    "__init__.pyi",  # Python stub package init
    "mod.rs",        # Rust module root (pub mod declarations live here)
    "index.ts",      # TypeScript barrel (re-exports the module's public surface)
    "index.tsx",     # TypeScript/JSX barrel
    "index.js",      # JavaScript barrel
    "index.jsx",     # JavaScript/JSX barrel
})


def is_package_file(path: str | None) -> bool:
    """Return True if path is a package-plumbing barrel file; False otherwise.

    A "package file" is a conventional entry-point that re-exports or declares
    a module's public API (__init__.py, mod.rs, index.ts, etc.). These files
    tend to accumulate high degree in the call graph because every importer of
    the package creates an edge through them — but they carry no independent
    production logic, so they pollute ranked surfaces like the hub list.

    Args:
        path: Absolute or relative file path string, or None.

    Returns:
        True  — file is a recognized package-plumbing barrel.
        False — normal source file, unresolved, or path is None/empty.

    Never raises; None or empty string returns False.
    """
    # Guard: None or empty is treated as unknown → not a package file.
    if not path:
        return False

    # Only the basename matters — any directory containing __init__.py is fine.
    # Case-sensitive: package filenames are lowercase by convention on all platforms.
    return Path(path).name in _PACKAGE_BASENAMES
