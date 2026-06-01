"""File watcher daemon — watchdog-based auto-sync for the Seam index.

SeamWatcher extends watchdog's FileSystemEventHandler.
Debounces rapid saves with threading.Timer (default: 500ms from config).
PID file written to .seam/watcher.pid for seam status to check.
"""

from pathlib import Path

# Implementations: see IMPLEMENTATION_PLAN.md step 7.1


class SeamWatcher:
    """Watchdog-based file system event handler with debounced re-indexing."""

    def __init__(self, db_path: Path, root_path: Path) -> None:
        raise NotImplementedError("Implement in step 7.1")

    def start(self) -> None:
        """Start the watchdog Observer in a background thread."""
        raise NotImplementedError("Implement in step 7.1")

    def stop(self) -> None:
        """Stop the watchdog Observer and clean up PID file."""
        raise NotImplementedError("Implement in step 7.1")
