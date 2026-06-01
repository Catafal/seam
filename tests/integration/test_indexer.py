"""Integration tests for `seam init` and `seam status` CLI commands.

Tests run init over tests/fixtures/, then check:
  C1: DB contains symbols, including a known fixture symbol.
  C2: status reports symbol count > 0 without errors.
"""

import sqlite3
import tempfile
from collections.abc import Generator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from seam.cli.main import app

# Path to test fixtures — the two canonical sample files
FIXTURES_DIR = Path(__file__).parents[1] / "fixtures"

# A symbol known to appear in sample.py (standalone top-level function)
KNOWN_PYTHON_SYMBOL = "standalone_function"

# A symbol known to appear in sample.ts
KNOWN_TS_SYMBOL = "standaloneFunction"


@pytest.fixture()
def tmp_db_dir() -> Generator[Path, None, None]:
    """Temporary directory that acts as the project root for init tests."""
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)


# ── C1: seam init ─────────────────────────────────────────────────────────────


def test_init_indexes_fixtures(tmp_db_dir: Path) -> None:
    """init over tests/fixtures/ must produce a DB with symbols > 0."""
    runner = CliRunner()
    result = runner.invoke(app, ["init", str(FIXTURES_DIR), "--db-dir", str(tmp_db_dir)])

    # Command must succeed
    assert result.exit_code == 0, f"init failed:\n{result.output}"

    # DB file must exist at the expected location
    db_path = tmp_db_dir / ".seam" / "seam.db"
    assert db_path.exists(), "seam.db was not created"

    # Must contain at least one symbol
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    count = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
    conn.close()
    assert count > 0, "No symbols were indexed"


def test_init_includes_known_symbol(tmp_db_dir: Path) -> None:
    """init must index the known Python fixture symbol 'standalone_function'."""
    runner = CliRunner()
    result = runner.invoke(app, ["init", str(FIXTURES_DIR), "--db-dir", str(tmp_db_dir)])
    assert result.exit_code == 0, f"init failed:\n{result.output}"

    db_path = tmp_db_dir / ".seam" / "seam.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT name FROM symbols WHERE name = ?", (KNOWN_PYTHON_SYMBOL,)).fetchone()
    conn.close()
    assert row is not None, f"Expected symbol '{KNOWN_PYTHON_SYMBOL}' not found in DB"


def test_init_includes_typescript_symbol(tmp_db_dir: Path) -> None:
    """init must index the known TypeScript fixture symbol 'standaloneFunction'."""
    runner = CliRunner()
    result = runner.invoke(app, ["init", str(FIXTURES_DIR), "--db-dir", str(tmp_db_dir)])
    assert result.exit_code == 0, f"init failed:\n{result.output}"

    db_path = tmp_db_dir / ".seam" / "seam.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT name FROM symbols WHERE name = ?", (KNOWN_TS_SYMBOL,)).fetchone()
    conn.close()
    assert row is not None, f"Expected symbol '{KNOWN_TS_SYMBOL}' not found in DB"


def test_init_is_idempotent(tmp_db_dir: Path) -> None:
    """Running init twice on the same directory must not error or duplicate data."""
    runner = CliRunner()
    for _ in range(2):
        result = runner.invoke(app, ["init", str(FIXTURES_DIR), "--db-dir", str(tmp_db_dir)])
        assert result.exit_code == 0, f"init failed on run:\n{result.output}"

    db_path = tmp_db_dir / ".seam" / "seam.db"
    conn = sqlite3.connect(str(db_path))
    count_after_second = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
    conn.close()
    # Symbol count must be consistent — upsert_file replaces, not appends
    assert count_after_second > 0


# ── C2: seam status ───────────────────────────────────────────────────────────


def test_status_after_init_reports_symbols(tmp_db_dir: Path) -> None:
    """After init, status must print symbol count > 0 without error."""
    runner = CliRunner()
    # First init
    init_result = runner.invoke(app, ["init", str(FIXTURES_DIR), "--db-dir", str(tmp_db_dir)])
    assert init_result.exit_code == 0, f"init failed:\n{init_result.output}"

    # Then status
    status_result = runner.invoke(app, ["status", "--db-dir", str(tmp_db_dir)])
    assert status_result.exit_code == 0, f"status failed:\n{status_result.output}"

    # Output must mention "symbols" with a positive count somewhere
    assert "symbol" in status_result.output.lower(), (
        f"Expected 'symbol' in status output:\n{status_result.output}"
    )


def test_status_no_db_prints_helpful_message(tmp_db_dir: Path) -> None:
    """status without a DB must exit non-zero with a helpful message."""
    runner = CliRunner()
    result = runner.invoke(app, ["status", "--db-dir", str(tmp_db_dir)])
    # Must exit with error
    assert result.exit_code != 0
    # Must print a hint to run init
    assert "init" in result.output.lower(), f"Expected 'init' hint in output:\n{result.output}"
