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


def test_walk_skips_static_site_output_dirs(tmp_path: Path) -> None:
    """mkdocs/Jekyll output dirs (site/, _site/) are skipped — Gap 2 cluster noise."""
    (tmp_path / "app.py").write_text("def f():\n    return 1\n")
    for d in ("site", "_site"):
        sub = tmp_path / d
        sub.mkdir()
        (sub / "page.js").write_text("function page(){return 1}\n")

    found = {p.name for p in walk_project(tmp_path)}
    assert "app.py" in found
    assert "page.js" not in found, "static-site output (site/, _site/) must be skipped"


def test_walk_skips_minified_files(tmp_path: Path) -> None:
    """Minified bundles (*.min.js) are skipped wherever they live — they inject
    garbage 'symbols' that pollute clustering (benchmark Gap 2)."""
    (tmp_path / "app.py").write_text("def f():\n    return 1\n")
    (tmp_path / "real.js").write_text("function real(){return 1}\n")
    # The real culprit: a minified bundle in an assets dir (e.g. mkdocs-material).
    js = tmp_path / "assets" / "javascripts"
    js.mkdir(parents=True)
    (js / "bundle.min.js").write_text("function a(){};function b(){};\n")
    (tmp_path / "vendor_lib.min.js").write_text("var x=1;\n")

    found = {p.name for p in walk_project(tmp_path)}
    assert "app.py" in found
    assert "real.js" in found, "ordinary source must still be indexed"
    assert "bundle.min.js" not in found, "minified bundles must be skipped"
    assert "vendor_lib.min.js" not in found


def test_skip_dirs_contains_static_site(tmp_path: Path) -> None:
    """site/ is in the skip set (mkdocs default output dir)."""
    assert "site" in SKIP_DIRS
