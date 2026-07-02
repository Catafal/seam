"""Smoke tests — verify the package structure is intact.

These tests pass from Day 0 with no implementation.
They verify imports work and the version is set.
"""

import json
import re
import tomllib
from pathlib import Path

import seam


def test_package_version() -> None:
    """seam.__version__ must match pyproject.toml [project].version.

    WHY assert equality (not a hardcoded literal): the two version sources drifted
    once (__init__ at 0.2.0 while pyproject was 0.2.1), which a hardcoded-literal
    test could not catch and a literal test breaks on every bump for no reason.
    Pinning them to each other catches the real drift AND survives version bumps.
    """
    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    pyproject_version = tomllib.loads(pyproject.read_text())["project"]["version"]
    assert seam.__version__ == pyproject_version
    # Sanity: it is a semver-shaped string (not empty / not a placeholder).
    assert re.fullmatch(r"\d+\.\d+\.\d+", seam.__version__), seam.__version__


def test_config_imports() -> None:
    from seam.config import SEAM_DB_PATH, SEAM_LANGUAGE_MAP

    assert SEAM_DB_PATH == ".seam/seam.db"
    assert ".py" in SEAM_LANGUAGE_MAP
    assert ".ts" in SEAM_LANGUAGE_MAP


def test_npm_package_version_matches_pyproject() -> None:
    """pkg/npm/package.json version must equal pyproject.toml version.

    WHY this runs in make gate (not make test-npm): version drift between the npm
    shim and the PyPI distribution makes `@catafal/seam@X.Y.Z` install a different
    seam-code version — breaking the reproducibility guarantee. The Python gate
    catches it without requiring Node, using only stdlib json + tomllib.
    """
    root = Path(__file__).resolve().parents[2]
    pyproject_version = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
    npm_pkg = root / "pkg" / "npm" / "package.json"
    npm_version = json.loads(npm_pkg.read_text())["version"]
    assert npm_version == pyproject_version, (
        f"pkg/npm/package.json version ({npm_version!r}) does not match "
        f"pyproject.toml version ({pyproject_version!r}). "
        "Bump them in lockstep when releasing."
    )


def test_fts5_available() -> None:
    """Verify SQLite FTS5 extension is available on this machine."""
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE VIRTUAL TABLE t USING fts5(content)")
    conn.close()
