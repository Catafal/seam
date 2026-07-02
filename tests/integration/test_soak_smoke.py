"""Smoke test for P5.5 S5 — benchmarks/soak.py.

NOT the full soak (that's a local/optional-CI tool). This just proves the harness
runs a small iteration count against a real index without error, and that under
SEAM_DIAGNOSTICS=1 it produces an NDJSON trace. Kept tiny so it stays in the suite.
"""

import json
from pathlib import Path

import pytest

import seam.analysis.diagnostics as diagnostics
import seam.config as config
from benchmarks import soak
from seam.indexer.db import init_db, upsert_file
from seam.indexer.graph import Edge, Symbol


@pytest.fixture(autouse=True)
def _reset_diag():
    diagnostics.reset_recorder()
    yield
    diagnostics.reset_recorder()


def _build_index(root: Path) -> None:
    db_path = root / ".seam" / "seam.db"
    db_path.parent.mkdir(parents=True)
    conn = init_db(db_path)
    src = root / "m.py"
    src.write_text("# stub\n")
    symbols = [
        Symbol(name="alpha", kind="function", file=str(src), start_line=1, end_line=2, docstring=""),
        Symbol(name="beta", kind="function", file=str(src), start_line=3, end_line=4, docstring=""),
    ]
    edges = [
        Edge(source="beta", target="alpha", kind="call", file=str(src), line=3, confidence="EXTRACTED"),
    ]
    upsert_file(conn, src, "python", "sha", symbols, edges)
    conn.close()


def test_soak_runs_without_diagnostics(tmp_path: Path) -> None:
    """A small soak run returns 0 even when diagnostics is off."""
    _build_index(tmp_path)
    rc = soak.main(["--iterations", "8", "--path", str(tmp_path)])
    assert rc == 0


def test_soak_no_index_errors(tmp_path: Path) -> None:
    """Soak on a directory with no index returns a non-zero exit code."""
    rc = soak.main(["--iterations", "4", "--path", str(tmp_path)])
    assert rc == 1


def test_soak_writes_ndjson_when_enabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Under SEAM_DIAGNOSTICS=1 the soak run produces an NDJSON trace."""
    _build_index(tmp_path)
    ndjson = tmp_path / "diag.ndjson"
    monkeypatch.setattr(config, "SEAM_DIAGNOSTICS", "1")
    monkeypatch.setattr(config, "SEAM_DIAGNOSTICS_PATH", str(ndjson))
    monkeypatch.setattr(config, "SEAM_DIAGNOSTICS_SLOW_MS", 0)  # force slow_query lines

    rc = soak.main(["--iterations", "12", "--path", str(tmp_path)])
    assert rc == 0
    assert ndjson.exists()
    lines = [json.loads(ln) for ln in ndjson.read_text().splitlines() if ln.strip()]
    assert any(ln["event"] == "slow_query" for ln in lines)
    # No source text leaks (fixture has none, but assert the size proxy is numeric).
    for ln in lines:
        if ln["event"] == "slow_query":
            assert isinstance(ln["result_chars"], int)
