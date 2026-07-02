"""File watcher daemon — watchdog-based auto-sync for the Seam index.

SeamWatcher extends watchdog's FileSystemEventHandler.

Debounce strategy:
  - Each source file that fires an event gets its own threading.Timer.
  - If the same file fires again before the timer expires the old timer is
    cancelled and a new one is started. This prevents hammering the indexer
    on rapid editor saves (which often write the file several times per save).
  - Debounce delay is read from config.SEAM_DEBOUNCE_MS (default 500 ms).

DB lifetime:
  - One SQLite connection is opened at start() and closed at stop().
  - All debounce callbacks run on the watchdog Observer thread pool, so the
    connection is shared across callbacks. SQLite handles this safely because
    watchdog's per-directory threads serialise event delivery.

PID file:
  - start() writes os.getpid() to <db_path.parent>/watcher.pid
  - stop() removes it; seam status reads it to show watcher state.
"""

import logging
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

import seam.config as config
from seam.indexer.config_resources import is_config_resource_file
from seam.indexer.db import connect, delete_file
from seam.indexer.pipeline import index_one_file
from seam.indexer.test_edges import index_test_edges

logger = logging.getLogger(__name__)


class SeamWatcher(FileSystemEventHandler):
    """Watchdog event handler with per-file debounced re-indexing.

    Usage:
        watcher = SeamWatcher(db_path=..., root_path=...)
        watcher.start()
        # ... file system events are handled automatically ...
        watcher.stop()
    """

    def __init__(self, db_path: Path, root_path: Path) -> None:
        super().__init__()

        self._db_path = db_path
        # Resolve the root so watchdog event paths (watched-root + relative)
        # match the resolved-absolute paths that `seam init` stores. Without
        # this, macOS /var vs /private/var divergence produces duplicate
        # `files` rows and orphaned symbols on the watcher path.
        self._root_path = root_path.resolve()

        # DB connection — opened by start(), closed by stop()
        self._conn: sqlite3.Connection | None = None

        # Watchdog observer — created at start(), joined at stop()
        # Typed as Any because watchdog's Observer is a module-level variable
        # resolved at runtime (not a stable class in stub), causing mypy error.
        self._observer: Any = None

        # Per-file debounce timers. key = str(absolute path), value = active Timer.
        self._timers: dict[str, threading.Timer] = {}

        # Lock to serialise access to _timers dict (safety for multi-threaded watchdog)
        self._timer_lock = threading.Lock()

        # Debounce delay in seconds (config is in ms)
        self._debounce_s: float = config.SEAM_DEBOUNCE_MS / 1000.0

        # PID file path sits next to the DB
        self._pid_file: Path = db_path.parent / "watcher.pid"

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the watchdog Observer in a background thread.

        Opens the DB connection, writes the PID file, schedules the observer
        to watch root_path recursively, then starts the observer thread.
        """
        logger.info("SeamWatcher starting — root=%s db=%s", self._root_path, self._db_path)

        # Open via connect() (sets foreign_keys=ON + busy_timeout) with
        # check_same_thread=False so the observer thread and timer callbacks
        # can share it. foreign_keys MUST be on here — this is the only
        # long-lived WRITER, so without it re-index/delete don't cascade.
        self._conn = connect(self._db_path, check_same_thread=False)

        # Write PID file so `seam status` can report watcher state
        self._pid_file.write_text(str(os.getpid()))
        logger.debug("PID file written: %s (pid=%d)", self._pid_file, os.getpid())

        # Create and start the watchdog observer
        self._observer = Observer()
        self._observer.schedule(self, str(self._root_path), recursive=True)
        self._observer.start()

        logger.info("SeamWatcher started (debounce=%.0f ms)", config.SEAM_DEBOUNCE_MS)

    def stop(self) -> None:
        """Stop the watchdog Observer, cancel pending timers, and clean up.

        Idempotent — safe to call multiple times.
        """
        logger.info("SeamWatcher stopping")

        # Cancel any pending debounce timers
        with self._timer_lock:
            for timer in self._timers.values():
                timer.cancel()
            self._timers.clear()

        # Stop the watchdog observer
        if self._observer is not None:
            self._observer.stop()
            self._observer.join()
            self._observer = None
            logger.debug("Observer stopped and joined")

        # Close DB connection
        if self._conn is not None:
            self._conn.close()
            self._conn = None
            logger.debug("DB connection closed")

        # Remove PID file
        try:
            self._pid_file.unlink(missing_ok=True)
            logger.debug("PID file removed: %s", self._pid_file)
        except OSError as exc:
            logger.warning("Could not remove PID file %s: %s", self._pid_file, exc)

        logger.info("SeamWatcher stopped")

    # ── watchdog event handlers ────────────────────────────────────────────────

    def on_created(self, event: FileSystemEvent) -> None:
        """Handle file creation — schedule a debounced index of the new file."""
        if event.is_directory:
            return
        self._schedule_index(Path(str(event.src_path)))

    def on_modified(self, event: FileSystemEvent) -> None:
        """Handle file modification — schedule a debounced re-index."""
        if event.is_directory:
            return
        self._schedule_index(Path(str(event.src_path)))

    def on_deleted(self, event: FileSystemEvent) -> None:
        """Handle file deletion — remove from the DB immediately (no debounce needed)."""
        if event.is_directory:
            return

        path = Path(str(event.src_path))

        # Cancel any pending index timer for this file (it no longer exists)
        with self._timer_lock:
            timer = self._timers.pop(str(path), None)
        if timer is not None:
            timer.cancel()

        logger.debug("File deleted: %s — removing from DB", path)
        self._do_delete(path)

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _schedule_index(self, path: Path) -> None:
        """Cancel any existing timer for path, start a fresh debounce timer.

        Only schedules source files and safe config/resource files.
        """
        if path.suffix.lower() not in config.SEAM_LANGUAGE_MAP and not is_config_resource_file(path):
            return  # not a file we care about

        key = str(path)
        with self._timer_lock:
            # Cancel existing timer for this path if any
            existing = self._timers.pop(key, None)
            if existing is not None:
                existing.cancel()

            # Schedule new timer — callback runs after debounce delay
            timer = threading.Timer(self._debounce_s, self._do_index, args=(path,))
            self._timers[key] = timer
            timer.start()
            logger.debug("Debounce timer set for %s (%.0f ms)", path.name, config.SEAM_DEBOUNCE_MS)

    def _do_index(self, path: Path) -> None:
        """Debounce callback: index a single file into the DB.

        Runs on a timer thread. Guards against missing conn (watcher was stopped).
        """
        # Remove the timer entry (it has already fired)
        with self._timer_lock:
            self._timers.pop(str(path), None)

        conn = self._conn
        if conn is None:
            logger.debug("_do_index: watcher stopped, skipping %s", path)
            return

        # Runs on a Timer thread with no caller — any unhandled exception would
        # die silently and leave the index stale without warning. Log it.
        try:
            logger.debug("Indexing file: %s", path)
            result = index_one_file(conn, path)
            if result is None:
                logger.debug("Skipped %s (unsupported/binary/error)", path)
            else:
                index_test_edges(conn)
                logger.info("Indexed %s — %d symbols, %d edges", path.name, result[0], result[1])
        except Exception:  # noqa: BLE001 — never let a re-index failure crash the daemon silently
            logger.exception("re-index failed for %s — index may be stale", path)

    def _do_delete(self, path: Path) -> None:
        """Remove a file's index entries from the DB.

        Runs on the watchdog event thread. Guards against missing conn.
        """
        conn = self._conn
        if conn is None:
            logger.debug("_do_delete: watcher stopped, skipping %s", path)
            return

        # Runs on the watchdog event thread — guard so a delete failure is
        # logged rather than killing the event dispatch thread silently.
        try:
            delete_file(conn, path)
            index_test_edges(conn)
            logger.info("Removed %s from index", path.name)
        except Exception:  # noqa: BLE001
            logger.exception("delete failed for %s", path)
