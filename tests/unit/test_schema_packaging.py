"""Guard against the distribution bug where `seam init` could not find its SQL schema.

The schema lives at docs/database/schema.sql (canonical) but docs/ is NOT shipped in the
wheel, so a real `pip install` crashed on init (FileNotFoundError). db.py now prefers a
packaged copy (seam/_data/schema.sql, force-included by hatch) with a dev-checkout fallback.
These tests guard BOTH the runtime resolution and the packaging config so the bug can't return.
"""

import tomllib
from pathlib import Path

from seam.indexer.db import _SCHEMA_PATH


def test_schema_path_resolves_and_has_tables() -> None:
    assert _SCHEMA_PATH.exists(), f"schema not found at {_SCHEMA_PATH}"
    assert "CREATE TABLE" in _SCHEMA_PATH.read_text()


def test_wheel_force_includes_schema_into_package() -> None:
    root = Path(__file__).parents[2]
    cfg = tomllib.loads((root / "pyproject.toml").read_text())
    force_include = cfg["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]
    # The wheel must ship the schema inside the package, or installed `seam init` breaks.
    assert force_include.get("docs/database/schema.sql") == "seam/_data/schema.sql"
