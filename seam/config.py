"""Seam configuration — all settings read from environment with sensible defaults."""

import os
from pathlib import Path

# Path to the SQLite database, relative to the project root that runs `seam`
SEAM_DB_PATH: str = os.getenv("SEAM_DB_PATH", ".seam/seam.db")

# Logging level: DEBUG | INFO | WARNING | ERROR
SEAM_LOG_LEVEL: str = os.getenv("SEAM_LOG_LEVEL", "INFO")

# Debounce delay for file watcher (milliseconds)
SEAM_DEBOUNCE_MS: int = int(os.getenv("SEAM_DEBOUNCE_MS", "500"))

# Maximum file size to index (bytes). Files above this are silently skipped.
SEAM_MAX_FILE_BYTES: int = int(os.getenv("SEAM_MAX_FILE_BYTES", str(1024 * 1024)))  # 1MB

# Maximum number of changed symbol names to run impact() on in one detect_changes call.
# If the diff touches more real symbols than this cap, only the first N are analyzed
# and ChangeReport.partial is set to True. Raise via env var on large codebases.
SEAM_MAX_IMPACT_SYMBOLS: int = int(os.getenv("SEAM_MAX_IMPACT_SYMBOLS", "50"))

# File extensions to index, mapped to language identifier
SEAM_LANGUAGE_MAP: dict[str, str] = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
}


def get_db_path(project_root: Path) -> Path:
    """Resolve the database path relative to the project root."""
    return project_root / SEAM_DB_PATH
