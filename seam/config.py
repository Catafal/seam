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
    ".go": "go",
    ".rs": "rust",
}


# ── Phase 2: Clustering configuration ────────────────────────────────────────

# Cluster naming mode: "deterministic" (default) or "llm" (opt-in).
# When set to "llm", SEAM_LLM_API_KEY must also be set or naming falls back
# to "deterministic". LLM naming runs only during `seam init`, never in MCP.
SEAM_CLUSTER_NAMING: str = os.getenv("SEAM_CLUSTER_NAMING", "deterministic")

# Optional LLM API key for cluster naming (only used when SEAM_CLUSTER_NAMING=llm).
# When absent/empty, LLM naming is silently skipped and deterministic is used.
SEAM_LLM_API_KEY: str | None = os.getenv("SEAM_LLM_API_KEY") or None

# LLM model for cluster naming. Uses a small/fast model by default.
SEAM_LLM_MODEL: str = os.getenv("SEAM_LLM_MODEL", "gpt-4o-mini")

# Minimum cluster size. Communities with fewer distinct graph nodes than this
# are NOT persisted as clusters — their symbols get cluster_id=NULL (unclustered).
# Default 2: kills pure singletons (symbols with no edges) so `seam clusters`
# shows functional areas, not 200 one-symbol rows.
# Set to 1 to retain all singletons as their own clusters.
SEAM_CLUSTER_MIN_SIZE: int = int(os.getenv("SEAM_CLUSTER_MIN_SIZE", "2"))


# ── Phase 3: Search / fuzzy fallback configuration ───────────────────────────

# Maximum Damerau-Levenshtein edit distance for the fuzzy fallback.
# Applied when both FTS and LIKE fallbacks return zero rows.
# 1 = catch single-char typos (conservative); 2 = broader (may add noise).
SEAM_FUZZY_MAX_DIST: int = int(os.getenv("SEAM_FUZZY_MAX_DIST", "1"))

# Maximum candidate symbol names to evaluate in the fuzzy fallback.
# Caps the O(n) edit-distance scan over distinct symbol names.
# On very large codebases, raise this via env var if precision matters more.
SEAM_FUZZY_MAX_CANDIDATES: int = int(os.getenv("SEAM_FUZZY_MAX_CANDIDATES", "500"))


def get_db_path(project_root: Path) -> Path:
    """Resolve the database path relative to the project root."""
    return project_root / SEAM_DB_PATH
