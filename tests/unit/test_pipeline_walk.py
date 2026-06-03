"""Tests for walk_project's directory-skipping (build-artifact exclusion).

Regression guard for the Explorer Phase 2.1 fix: `seam init` must NOT index
built/generated output (e.g. the minified SPA bundle in seam/_web/assets/),
which injected garbage symbols and polluted clustering.
"""

from pathlib import Path

from seam.indexer.pipeline import SKIP_DIRS, walk_project


def test_walk_skips_build_output_dirs(tmp_path: Path) -> None:
    """Files inside build/output/vendor dirs are not returned; source is."""
    # A real source file that MUST be indexed.
    (tmp_path / "app.py").write_text("def f():\n    return 1\n")

    # Built/generated/vendor dirs that MUST be skipped.
    for d in ("_web", "dist", "build", "node_modules", "out", "target", "vendor"):
        sub = tmp_path / d
        sub.mkdir()
        (sub / "junk.py").write_text("def junk():\n    return 0\n")
    # Nested case: the real bug — seam/_web/assets/index-*.js
    assets = tmp_path / "_web" / "assets"
    assets.mkdir()
    (assets / "index-ABC123.js").write_text("function bundled(){return 1}\n")

    found = {p.name for p in walk_project(tmp_path)}
    assert "app.py" in found
    assert "junk.py" not in found
    assert "index-ABC123.js" not in found


def test_skip_dirs_contains_web(tmp_path: Path) -> None:
    """The built SPA dir name is in the skip set (closes the seam/_web gap)."""
    assert "_web" in SKIP_DIRS
