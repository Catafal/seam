"""Guard that the 'web' optional-dependency group and wheel artifact config are present.

This mirrors test_schema_packaging.py — ensures the packaging config needed to ship the
built SPA (seam/_web/) in the wheel is never accidentally removed.  The built SPA is
gitignored (so hatch would normally skip it); the 'artifacts' key tells hatch to include
it anyway.  Without this the installed wheel would serve a 404 on every frontend request.
"""

import tomllib
from pathlib import Path

_ROOT = Path(__file__).parents[2]


def test_web_optional_dependency_exists() -> None:
    """The 'web' extra must be declared so users can do pip install 'seam-code[web]'."""
    cfg = tomllib.loads((_ROOT / "pyproject.toml").read_text())
    optional_deps = cfg["project"].get("optional-dependencies", {})
    assert "web" in optional_deps, "'web' extra not found in [project.optional-dependencies]"
    web_deps = optional_deps["web"]
    # Must include fastapi (case-insensitive normalised comparison)
    assert any("fastapi" in dep.lower() for dep in web_deps), (
        f"fastapi not listed in web extra: {web_deps}"
    )


def test_web_extra_in_dev_group() -> None:
    """fastapi must be in the dev dependency-group so test imports work without --extra."""
    cfg = tomllib.loads((_ROOT / "pyproject.toml").read_text())
    dev_deps = cfg.get("dependency-groups", {}).get("dev", [])
    assert any("fastapi" in dep.lower() for dep in dev_deps), (
        f"fastapi not found in [dependency-groups] dev: {dev_deps}"
    )


def test_wheel_artifacts_include_web_dir() -> None:
    """Wheel must declare seam/_web/ as an artifact so the gitignored built SPA ships."""
    cfg = tomllib.loads((_ROOT / "pyproject.toml").read_text())
    artifacts = cfg["tool"]["hatch"]["build"]["targets"]["wheel"].get("artifacts", [])
    # At least one glob must cover seam/_web/
    assert any("seam/_web" in entry for entry in artifacts), (
        f"seam/_web not found in wheel artifacts: {artifacts}"
    )
