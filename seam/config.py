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


# ── Phase 3: Affected-tests configuration ────────────────────────────────────

# Maximum hop depth for the affected-tests traversal.
# Controls how far upstream (through the call/import graph) the `seam affected`
# command walks from each changed symbol to find dependent test files.
# Higher values find more distant test files but increase runtime on large graphs.
SEAM_AFFECTED_DEPTH: int = int(os.getenv("SEAM_AFFECTED_DEPTH", "5"))

# Maximum number of changed files accepted by handle_seam_affected.
# Inputs larger than this are rejected with INVALID_INPUT (agent mistake guard).
# Mirrors the _clamp discipline used by other bounded handlers.
SEAM_MAX_AFFECTED_FILES: int = int(os.getenv("SEAM_MAX_AFFECTED_FILES", "200"))

# Maximum symbols analyzed per file in affected().
# When a file defines more symbols than this, only the first N are traversed
# and AffectedResult.partial is set to True.
# Reuses the SEAM_MAX_IMPACT_SYMBOLS env var pattern for consistency.
SEAM_MAX_AFFECTED_SYMBOLS: int = int(os.getenv("SEAM_MAX_AFFECTED_SYMBOLS", "50"))

# ── Phase 3: Search / fuzzy fallback configuration ───────────────────────────

# Maximum Damerau-Levenshtein edit distance for the fuzzy fallback.
# Applied when both FTS and LIKE fallbacks return zero rows.
# 1 = catch single-char typos (conservative); 2 = broader (may add noise).
SEAM_FUZZY_MAX_DIST: int = int(os.getenv("SEAM_FUZZY_MAX_DIST", "1"))

# Maximum candidate symbol names to evaluate in the fuzzy fallback.
# Caps the O(n) edit-distance scan over distinct symbol names.
# On very large codebases, raise this via env var if precision matters more.
SEAM_FUZZY_MAX_CANDIDATES: int = int(os.getenv("SEAM_FUZZY_MAX_CANDIDATES", "500"))


# ── Phase 4: Node-field enrichment configuration ─────────────────────────────

# Hard cap on stored signature length. Without a cap, a function with many type-annotated
# parameters can produce a 500+ character signature that dominates the FTS index and makes
# MCP responses painful to read. 300 chars captures the full header of all but pathological
# cases; truncation appends '...' so consumers can detect incomplete signatures.
SEAM_MAX_SIGNATURE_LEN: int = int(os.getenv("SEAM_MAX_SIGNATURE_LEN", "300"))


# ── Phase 5: Import Resolution configuration ─────────────────────────────────

# Master switch for builtin filtering. When "off", count==0 names always resolve
# to INFERRED 'unresolved' regardless of whether they are known builtins.
SEAM_BUILTIN_FILTERING: str = os.getenv("SEAM_BUILTIN_FILTERING", "on")

# Master switch for import-resolution promotion (step A). When "off", import
# mappings are not extracted at index time and not used for promotion at read time.
# The name-count rule (existing behavior) is used exclusively.
SEAM_IMPORT_RESOLUTION: str = os.getenv("SEAM_IMPORT_RESOLUTION", "on")

# Cap on candidate declaring files evaluated per import-resolution lookup (step A).
# Prevents runaway DB queries on pathologically ambiguous indexes.
SEAM_MAX_IMPORT_CANDIDATES: int = int(os.getenv("SEAM_MAX_IMPORT_CANDIDATES", "25"))

# Cap on collision candidates ranked by file-path proximity (step D).
# Prevents O(n) proximity computation on large symbol tables.
SEAM_PROXIMITY_MAX_CANDIDATES: int = int(os.getenv("SEAM_PROXIMITY_MAX_CANDIDATES", "25"))


# ── Phase 8: Lean output + impact cap ────────────────────────────────────────

# Per-tier entry cap for seam_impact. When a tier contains more entries than this
# cap, the list is sliced to this many entries (the closest/highest-risk first,
# since tiers are distance-ordered by construction). A summary count per tier
# (risk_summary) is always computed BEFORE capping so the histogram is honest.
# Set to 0 to disable the cap and return all entries.
# Default 25 prevents the "hub symbol dumps 200+ entries" problem (issue #33).
SEAM_IMPACT_MAX_RESULTS: int = int(os.getenv("SEAM_IMPACT_MAX_RESULTS", "25"))


# ── Phase 6: Context-Pack configuration ─────────────────────────────────────

# Maximum enriched callers AND maximum enriched callees in one context_pack bundle.
# When the raw neighbor list exceeds this, the list is truncated and the count of
# dropped entries is reported in ContextPack.truncated.callers/callees.
SEAM_PACK_NEIGHBOR_LIMIT: int = int(os.getenv("SEAM_PACK_NEIGHBOR_LIMIT", "10"))

# Maximum neighbor entries from any single file (homonym diversity cap).
# When a hot utility file defines many same-named symbols, capping per file
# keeps the bundle diverse across the codebase.
# Applied BEFORE the global neighbor limit (PRD §4.5a).
SEAM_PACK_PER_FILE_CAP: int = int(os.getenv("SEAM_PACK_PER_FILE_CAP", "3"))

# Maximum WHY/HACK/NOTE/TODO/FIXME comments in the bundle.
SEAM_PACK_MAX_COMMENTS: int = int(os.getenv("SEAM_PACK_MAX_COMMENTS", "10"))


def get_db_path(project_root: Path) -> Path:
    """Resolve the database path relative to the project root."""
    return project_root / SEAM_DB_PATH
