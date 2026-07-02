"""Shared indexing orchestration for `seam init` and `seam serve` auto-init.

WHY this module exists:
  `seam init` and `seam serve --auto-init` must share ONE code path so they can
  never drift apart. Previously, the full pipeline (init_db → .gitignore →
  walk_project → per-file loop → cluster post-pass → synthesis post-pass →
  test-edge post-pass → optional embeddings) was inlined inside main.py's `init`
  command. Extracting it here lets serve.py call the same function without any
  CLI dependency.

Interface contract:
  run_init(root, *, db_dir, semantic, progress_cb) → InitResult

  - root:         absolute project root (Path); must be a directory
  - db_dir:       optional DB directory override; defaults to root
  - semantic:     if True, also run embedding index after the main pipeline
  - progress_cb:  optional callable(str) — called with human-readable status
                  strings so callers can drive a spinner/progress display.
                  NEVER called with Rich markup — plain text only, so CLI,
                  serve, and tests can all consume it safely.

  Returns InitResult dataclass with all counters. Never raises (logs errors and
  returns failed-sentinel counts like -1 for optional post-passes).

Design rules (CLAUDE.md):
  - Max 200 lines per function; max 1000 lines per file.
  - All imports at top of file.
  - Config ONLY via seam.config.
  - No terminal rendering (no Rich here — that stays in cli/main.py).
  - Type hints: X | None not Optional[X].
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import seam.config as config
from seam.indexer.cluster_index import get_llm_naming_summary, index_clusters
from seam.indexer.db import init_db
from seam.indexer.embedding_index import index_embeddings
from seam.indexer.pipeline import index_one_file, walk_project
from seam.indexer.synthesis_index import index_synthesis
from seam.indexer.test_edges import index_test_edges

logger = logging.getLogger(__name__)


# ── Result type ───────────────────────────────────────────────────────────────


@dataclass
class InitResult:
    """Structured result returned by run_init.

    Mirrors the counters tracked by the `seam init` command so the CLI can
    render its summary table purely from this object.

    Sentinel values (matching main.py conventions):
      total_clusters  : -1 = clustering failed; >=0 = count
      total_synthesis : -1 = synthesis failed; 0 = off or nothing produced; >=1 = count
      total_test_edges: -1 = failed; 0 = nothing produced; >=1 = count
      total_embeddings: None = not requested; 0 = skipped (fastembed absent);
                        -1 = failed; >=1 = count

    llm_naming_summary is only populated when SEAM_CLUSTER_NAMING="llm" and
    clustering produced at least one cluster.
    """

    db_path: Path
    indexed_files: int
    skipped_files: int
    total_symbols: int
    total_edges: int
    total_clusters: int
    total_synthesis: int
    total_test_edges: int
    total_embeddings: int | None  # None = semantic not requested
    llm_naming_summary: str | None


# ── Private helpers ───────────────────────────────────────────────────────────


def _ensure_gitignore(db_path: Path) -> None:
    """Write a self-scoped .seam/.gitignore containing '*' if it does not exist.

    WHY: keeps the index (db/-shm/-wal) out of git so `seam_changes` never
    reports Seam's own artifacts as changed files. Written INSIDE .seam/ —
    Seam touches nothing outside .seam/ (cleanliness guarantee). Idempotent.
    """
    gitignore = db_path.parent / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text("*\n", encoding="utf-8")


def _notify(progress_cb: Callable[[str], None] | None, message: str) -> None:
    """Call progress_cb with a plain-text status message, if one was provided.

    WHY centralized: avoids scattered `if progress_cb:` checks and makes it
    easy to see all the progress steps at a glance.
    """
    if progress_cb is not None:
        progress_cb(message)


# ── Main entry point ──────────────────────────────────────────────────────────


def run_init(
    root: Path,
    *,
    db_dir: Path | None = None,
    semantic: bool = False,
    progress_cb: Callable[[str], None] | None = None,
) -> InitResult:
    """Run the full indexing pipeline and return structured counts.

    This is the canonical, shared implementation of the indexing pipeline.
    Both `seam init` and `seam serve` auto-init call this function so the
    two code paths can never drift.

    Args:
        root:        Project root directory (must exist).
        db_dir:      Override the directory that holds the .seam/ subfolder.
                     Defaults to root. Matches the --db-dir CLI option.
        semantic:    If True, also embed all symbols after indexing.
                     Requires the [semantic] extra (fastembed); degrades
                     gracefully (returns total_embeddings=0) when absent.
        progress_cb: Optional callable(str) called with plain-text status
                     messages. May be None. Never receives Rich markup.

    Returns:
        InitResult with all counter fields populated.

    Design: never raises. Optional post-passes (clustering, synthesis,
    test edges, embeddings) return sentinel values (-1 = failed) on error
    and log a WARNING so the caller can surface it without crashing.
    """
    # Resolve DB root: --db-dir overrides for test isolation (mirrors main.py).
    db_root = db_dir if db_dir is not None else root
    db_path = config.get_db_path(db_root)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    # Step 0: write the self-scoped .gitignore so git ignores .seam/ entirely.
    _ensure_gitignore(db_path)

    # Step 1: collect files to index.
    files = walk_project(root)
    _notify(progress_cb, f"Indexing {len(files)} file(s)...")

    total_symbols = 0
    total_edges = 0
    indexed_files = 0
    skipped_files = 0

    # Step 2: per-file extraction loop.
    conn = init_db(db_path)
    try:
        for file_path in files:
            _notify(progress_cb, f"Indexing {file_path.name}...")
            # Returns None when skipped (binary/oversize/parse error);
            # (symbols, edges) when indexed (even if both are 0).
            result = index_one_file(conn, file_path)
            if result is None:
                skipped_files += 1
                continue
            indexed_files += 1
            total_symbols += result[0]
            total_edges += result[1]

        # Step 3: clustering post-pass (whole-graph; must see complete graph).
        _notify(progress_cb, "Computing graph clusters...")
        total_clusters = index_clusters(
            conn,
            naming_mode=config.SEAM_CLUSTER_NAMING,
            llm_api_key=config.SEAM_LLM_API_KEY,
            llm_model=config.SEAM_LLM_MODEL,
            min_size=config.SEAM_CLUSTER_MIN_SIZE,
        )

        # LLM naming summary — only relevant when LLM naming was requested.
        llm_naming_summary: str | None = None
        if config.SEAM_CLUSTER_NAMING == "llm" and total_clusters > 0:
            llm_naming_summary = get_llm_naming_summary(conn)

        # Step 4: edge-synthesis post-pass.
        _notify(progress_cb, "Synthesizing dispatch edges...")
        total_synthesis = index_synthesis(
            conn,
            enabled=config.SEAM_EDGE_SYNTHESIS == "on",
            fanout_cap=config.SEAM_SYNTHESIS_FANOUT_CAP,
        )

        # Step 5: test-edge materialization post-pass.
        _notify(progress_cb, "Materializing test edges...")
        total_test_edges = index_test_edges(conn)

        # Step 6 (optional): semantic embeddings.
        total_embeddings: int | None = None
        if semantic:
            _notify(progress_cb, "Computing symbol embeddings...")
            total_embeddings = index_embeddings(
                conn,
                model=config.SEAM_EMBED_MODEL,
                batch=32,
            )

    finally:
        conn.close()

    return InitResult(
        db_path=db_path,
        indexed_files=indexed_files,
        skipped_files=skipped_files,
        total_symbols=total_symbols,
        total_edges=total_edges,
        total_clusters=total_clusters,
        total_synthesis=total_synthesis,
        total_test_edges=total_test_edges,
        total_embeddings=total_embeddings,
        llm_naming_summary=llm_naming_summary,
    )
