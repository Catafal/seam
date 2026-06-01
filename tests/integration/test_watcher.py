"""Integration tests for SeamWatcher daemon (step 7.2).

TDD approach:
  - Start a real SeamWatcher on a temp directory.
  - Write a Python source file.
  - Poll the DB for up to 2 s, assert the new symbol appears.
  - Delete the file, poll for up to 2 s, assert the file row is gone.

These tests use real watchdog OS events (inotify on Linux, kqueue on macOS).
They are deliberately short (2 s timeout) and designed to be reliable even on
slow CI because the debounce default is only 500 ms.
"""

import sqlite3
import tempfile
import time
from pathlib import Path

import pytest

from seam.indexer.db import init_db
from seam.watcher.daemon import SeamWatcher

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def watch_env():
    """
    Provide an isolated temp dir, a DB path, and an open SeamWatcher.

    Yields (root_path, db_path, watcher) — the watcher is already started.
    Cleanup stops the watcher regardless of test outcome.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        seam_dir = root / ".seam"
        seam_dir.mkdir()
        db_path = seam_dir / "seam.db"

        # Initialise the DB schema before starting the watcher
        conn = init_db(db_path)
        conn.close()

        watcher = SeamWatcher(db_path=db_path, root_path=root)
        watcher.start()
        try:
            yield root, db_path, watcher
        finally:
            watcher.stop()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _poll_symbols(db_path: Path, name: str, timeout: float = 2.0) -> bool:
    """Poll the DB every 100 ms until 'name' appears in symbols or timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        conn = sqlite3.connect(str(db_path))
        row = conn.execute(
            "SELECT name FROM symbols WHERE name = ?", (name,)
        ).fetchone()
        conn.close()
        if row is not None:
            return True
        time.sleep(0.1)
    return False


def _poll_file_gone(db_path: Path, filepath: Path, timeout: float = 2.0) -> bool:
    """Poll the DB every 100 ms until 'filepath' is absent from files."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        conn = sqlite3.connect(str(db_path))
        row = conn.execute(
            "SELECT path FROM files WHERE path = ?", (str(filepath),)
        ).fetchone()
        conn.close()
        if row is None:
            return True
        time.sleep(0.1)
    return False


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_watcher_indexes_new_file(watch_env) -> None:
    """Writing a new .py file must update the DB with its symbols within 2 s."""
    root, db_path, _ = watch_env

    # Write a minimal Python file with one known function
    src = root / "hello.py"
    src.write_text("def greet_world():\n    pass\n")

    assert _poll_symbols(db_path, "greet_world", timeout=2.0), (
        "Symbol 'greet_world' did not appear in the DB within 2 s after file creation"
    )


def test_watcher_reindexes_modified_file(watch_env) -> None:
    """Modifying a .py file must update the DB with the new symbol within 2 s."""
    root, db_path, _ = watch_env

    # Write initial version
    src = root / "evolve.py"
    src.write_text("def old_function():\n    pass\n")

    # Wait briefly for initial index (or let the test be tolerant)
    time.sleep(0.3)

    # Overwrite with a new function
    src.write_text("def new_function():\n    pass\n")

    assert _poll_symbols(db_path, "new_function", timeout=2.0), (
        "Symbol 'new_function' did not appear in the DB within 2 s after file modification"
    )


def test_watcher_removes_deleted_file(watch_env) -> None:
    """Deleting an indexed .py file must remove it from the DB within 2 s."""
    root, db_path, _ = watch_env

    # Write and wait for indexing
    src = root / "temp_module.py"
    src.write_text("def temporary_func():\n    pass\n")

    # Wait for the file to be indexed first
    assert _poll_symbols(db_path, "temporary_func", timeout=2.0), (
        "Setup failed: 'temporary_func' never appeared before deletion test"
    )

    # Now delete it
    src.unlink()

    assert _poll_file_gone(db_path, src, timeout=2.0), (
        f"File '{src}' was not removed from the DB within 2 s after deletion"
    )


def test_watcher_pid_file_created_and_removed(watch_env) -> None:
    """start() must write .seam/watcher.pid; stop() must remove it."""
    root, db_path, watcher = watch_env
    pid_file = root / ".seam" / "watcher.pid"

    # PID file must exist after start()
    assert pid_file.exists(), ".seam/watcher.pid was not created by start()"
    pid_text = pid_file.read_text().strip()
    assert pid_text.isdigit(), f"watcher.pid does not contain a PID integer: {pid_text!r}"

    # stop() is called by the fixture teardown — but we can stop early and verify
    watcher.stop()
    assert not pid_file.exists(), ".seam/watcher.pid was not removed by stop()"
