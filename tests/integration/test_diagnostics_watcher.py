"""Integration tests for P5.5 S4 — watcher activity counters.

Drives SeamWatcher._do_index directly (deterministic, no debounce-thread timing)
and asserts the diagnostics recorder counts a successful re-index under
"reindexed" and a failed one under "reindex_errors". Counters are read back via
sample_resources — the same values that appear in an atexit snapshot line.
"""

import sqlite3
import tempfile
from pathlib import Path

import pytest

import seam.analysis.diagnostics as diagnostics
import seam.config as config
from seam.indexer.db import init_db
from seam.watcher import daemon
from seam.watcher.daemon import SeamWatcher


@pytest.fixture(autouse=True)
def _reset_diag():
    """Close + clear the diagnostics singleton around each test (no leaked atexit)."""
    diagnostics.reset_recorder()
    yield
    diagnostics.reset_recorder()


@pytest.fixture()
def enabled_watcher(monkeypatch: pytest.MonkeyPatch):
    """A watcher wired to a FRESH enabled diagnostics recorder + an open DB conn."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        db_path = root / ".seam" / "seam.db"
        db_path.parent.mkdir(parents=True)
        conn = init_db(db_path)

        monkeypatch.setattr(config, "SEAM_DIAGNOSTICS", "1")
        monkeypatch.setattr(config, "SEAM_DIAGNOSTICS_PATH", str(root / "diag.ndjson"))
        monkeypatch.setattr(config, "SEAM_DIAGNOSTICS_SLOW_MS", 0)
        # The autouse _reset_diag fixture has already cleared the singleton, so the
        # watcher's get_recorder() call below builds a fresh ENABLED recorder.
        watcher = SeamWatcher(db_path=db_path, root_path=root)
        watcher._conn = conn  # inject the conn _do_index needs (normally set by start())
        try:
            yield root, db_path, watcher
        finally:
            # Close the recorder (unregister its atexit handler) BEFORE the temp dir
            # is removed, so no dangling handler fires at interpreter exit.
            diagnostics.reset_recorder()
            conn.close()


def _counters(watcher: SeamWatcher, db_path: Path) -> dict:
    return watcher._recorder.sample_resources(str(db_path)) or {}


def test_successful_reindex_counted(enabled_watcher) -> None:
    """A real re-index of a source file increments the reindexed counter."""
    root, db_path, watcher = enabled_watcher
    src = root / "mod.py"
    src.write_text("def f():\n    return 1\n")

    watcher._do_index(src)

    metrics = _counters(watcher, db_path)
    assert metrics["watcher_reindexed"] == 1
    assert metrics["watcher_errors"] == 0


def test_failed_reindex_counted(
    enabled_watcher, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An exception during re-index increments reindex_errors, not reindexed."""
    root, db_path, watcher = enabled_watcher
    src = root / "mod.py"
    src.write_text("def f():\n    return 1\n")

    def _boom(_conn: sqlite3.Connection, _path: Path):
        raise RuntimeError("simulated re-index failure")

    monkeypatch.setattr(daemon, "index_one_file", _boom)

    watcher._do_index(src)  # must NOT raise — the daemon swallows + logs

    metrics = _counters(watcher, db_path)
    assert metrics["watcher_errors"] == 1
    assert metrics["watcher_reindexed"] == 0


def test_disabled_watcher_records_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """With SEAM_DIAGNOSTICS=0 the watcher's recorder is a null recorder (no counts)."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        db_path = root / ".seam" / "seam.db"
        db_path.parent.mkdir(parents=True)
        conn = init_db(db_path)
        try:
            monkeypatch.setattr(config, "SEAM_DIAGNOSTICS", "0")

            watcher = SeamWatcher(db_path=db_path, root_path=root)
            watcher._conn = conn
            src = root / "mod.py"
            src.write_text("def f():\n    return 1\n")
            watcher._do_index(src)

            # Null recorder → sample_resources returns None; nothing recorded.
            assert watcher._recorder.sample_resources(str(db_path)) is None
        finally:
            conn.close()
