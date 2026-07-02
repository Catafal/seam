"""Integration tests for P5.5 S3 — CLI read-command instrumentation.

These run the `seam` CLI as a REAL subprocess (not CliRunner) because the whole
point is the atexit snapshot flush, which only fires on genuine interpreter exit.
Each subprocess indexes nothing itself — the index is built in-process first, then
the CLI is pointed at it with --path.

Verifies: a real `seam search` invocation with SEAM_DIAGNOSTICS=1 appends a
slow_query line (forced via SEAM_DIAGNOSTICS_SLOW_MS=0) plus a final snapshot line
with query_count>=1, that no source text leaks, and that the disabled path writes
nothing.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

from seam.indexer.db import init_db, upsert_file
from seam.indexer.graph import Edge, Symbol

# Secret baked into the indexed docstring — must never reach the NDJSON.
_SECRET = "SECRET_TOKEN_sk_live_cli_cafebabe"

# Minimal in-process CLI driver: Typer reads sys.argv[1:], so argv after -c is the
# command line. Avoids depending on the installed console-script path.
_DRIVER = "from seam.cli.main import app; app()"


def _build_index(root: Path) -> None:
    """Create a real Seam index at root/.seam/seam.db with one seeded file."""
    db_path = root / ".seam" / "seam.db"
    db_path.parent.mkdir(parents=True)
    conn = init_db(db_path)
    src = root / "auth.py"
    src.write_text("# stub\n")
    symbols = [
        Symbol(
            name="authenticate_user",
            kind="function",
            file=str(src),
            start_line=1,
            end_line=2,
            docstring=_SECRET,
        ),
    ]
    edges: list[Edge] = []
    upsert_file(conn, src, "python", "sha", symbols, edges)
    conn.close()


def _run_cli(root: Path, ndjson: Path, *, enabled: bool, args: list[str]) -> None:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "SEAM_DIAGNOSTICS": "1" if enabled else "0",
        "SEAM_DIAGNOSTICS_PATH": str(ndjson),
        "SEAM_DIAGNOSTICS_SLOW_MS": "0",  # force every query to write a slow_query line
    }
    subprocess.run(
        [sys.executable, "-c", _DRIVER, *args],
        cwd=str(root),
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )


def _read(path: Path) -> list[dict]:
    return [json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()]


def test_cli_search_writes_query_and_snapshot(tmp_path: Path) -> None:
    """A real `seam search` subprocess records a slow_query line + an atexit snapshot."""
    _build_index(tmp_path)
    ndjson = tmp_path / "diag.ndjson"
    _run_cli(
        tmp_path, ndjson, enabled=True,
        args=["search", "authenticate", "--path", str(tmp_path), "--json"],
    )

    lines = _read(ndjson)
    slow = [ln for ln in lines if ln["event"] == "slow_query"]
    snaps = [ln for ln in lines if ln["event"] == "snapshot"]
    assert any(ln["tool"] == "seam_search" for ln in slow)
    assert len(snaps) >= 1  # atexit flush fired on real process exit
    assert snaps[-1]["query_count"] >= 1


def test_cli_no_source_text_leaks(tmp_path: Path) -> None:
    """The indexed secret docstring must never appear in the CLI-produced NDJSON."""
    _build_index(tmp_path)
    ndjson = tmp_path / "diag.ndjson"
    _run_cli(
        tmp_path, ndjson, enabled=True,
        args=["context", "authenticate_user", "--path", str(tmp_path), "--json"],
    )
    raw = ndjson.read_text()
    assert "SECRET_TOKEN" not in raw
    assert _SECRET not in raw


def test_cli_disabled_writes_nothing(tmp_path: Path) -> None:
    """With SEAM_DIAGNOSTICS=0 the CLI subprocess writes no diagnostics file."""
    _build_index(tmp_path)
    ndjson = tmp_path / "diag.ndjson"
    _run_cli(
        tmp_path, ndjson, enabled=False,
        args=["search", "authenticate", "--path", str(tmp_path), "--json"],
    )
    assert not ndjson.exists()
