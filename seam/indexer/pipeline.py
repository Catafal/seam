"""Indexing pipeline — the shared parse -> extract -> upsert path.

Lives in indexer/ (not cli/) because BOTH the CLI (`seam init`) and the watcher
daemon consume it. Putting it here keeps the import hierarchy intact:
  cli -> indexer.pipeline   and   watcher -> indexer.pipeline
(watcher importing from cli would violate the layer rules in BACKEND_STRUCTURE.md).

Pure-ish glue: no Typer, no watchdog — just stdlib + the indexer modules.
"""

import hashlib
import logging
import sqlite3
from pathlib import Path

import seam.config as config
from seam.analysis.imports import extract_import_mappings
from seam.indexer.db import upsert_file, upsert_import_mappings
from seam.indexer.graph import extract_comments, extract_edges, extract_symbols
from seam.indexer.parser import (
    parse_c,
    parse_cpp,
    parse_csharp,
    parse_go,
    parse_java,
    parse_javascript,
    parse_php,
    parse_python,
    parse_ruby,
    parse_rust,
    parse_swift,
    parse_typescript,
)

logger = logging.getLogger(__name__)

# Directories to skip when walking the project tree.
# Dot-dirs are skipped by default; this list catches common non-dot dirs.
SKIP_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        "node_modules",
        ".venv",
        "__pycache__",
        ".seam",
        "dist",
        "build",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
    }
)


def sha1(content: bytes) -> str:
    """Return the SHA-1 hex digest of file content bytes (change detection only)."""
    return hashlib.sha1(content).hexdigest()  # noqa: S324 — not used for security


def _dispatch_parser(path: Path, language: str):  # type: ignore[return]
    """Call the correct parser for a language string. Returns root Node or None."""
    if language == "python":
        return parse_python(path)
    if language == "typescript":
        return parse_typescript(path)
    if language == "javascript":
        return parse_javascript(path)
    if language == "go":
        return parse_go(path)
    if language == "rust":
        return parse_rust(path)
    # Phase 9 — new languages
    if language == "java":
        return parse_java(path)
    if language == "csharp":
        return parse_csharp(path)
    if language == "ruby":
        return parse_ruby(path)
    if language == "c":
        return parse_c(path)
    if language == "cpp":
        return parse_cpp(path)
    if language == "php":
        return parse_php(path)
    # Phase 10 — Swift
    if language == "swift":
        return parse_swift(path)
    return None


def index_one_file(conn: sqlite3.Connection, path: Path) -> tuple[int, int] | None:
    """Parse, extract, and upsert a single source file.

    Returns:
        (symbol_count, edge_count) when the file was INDEXED (upserted) — note
            this is (0, 0) for a valid-but-empty file like an empty __init__.py,
            which IS indexed.
        None when the file was SKIPPED (unsupported ext, oversize, binary/
            unreadable, or an error) — the reason is logged at DEBUG so a
            systematic failure (e.g. a grammar mismatch breaking every .ts file)
            is recoverable instead of silently invisible.

    Distinguishing None (skipped) from (0, 0) (indexed-but-empty) lets callers
    report an honest skipped-file count. Never raises.
    """
    try:
        ext = path.suffix.lower()
        language = config.SEAM_LANGUAGE_MAP.get(ext)
        if language is None:
            logger.debug("skip %s: unsupported extension", path)
            return None

        try:
            if path.stat().st_size > config.SEAM_MAX_FILE_BYTES:
                logger.debug("skip %s: over size limit", path)
                return None
        except OSError as exc:
            logger.debug("skip %s: stat failed: %s", path, exc)
            return None

        root = _dispatch_parser(path, language)
        if root is None:
            logger.debug("skip %s: parser returned None (binary/unreadable)", path)
            return None

        try:
            content = path.read_bytes()
        except OSError as exc:
            logger.debug("skip %s: read failed: %s", path, exc)
            return None

        file_hash = sha1(content)
        symbols = extract_symbols(root, language, path)
        # Pass symbols so extract_edges can resolve confidence (EXTRACTED/AMBIGUOUS/INFERRED)
        # against the same-file symbol set. Cross-file ambiguity is handled at query time.
        edges = extract_edges(root, language, path, symbols=symbols)
        # Extract semantic comments (WHY/HACK/NOTE/TODO/FIXME); never raises.
        comments = extract_comments(root, language, path)

        upsert_file(conn, path, language, file_hash, symbols, edges, comments)

        # Phase 5: extract and store import mappings for this file.
        # Only runs when SEAM_IMPORT_RESOLUTION is 'on' (default).
        # extract_import_mappings never raises; failures silently return [].
        if config.SEAM_IMPORT_RESOLUTION == "on":
            import_mappings = extract_import_mappings(root, path, language)
            upsert_import_mappings(conn, path, import_mappings)

        return len(symbols), len(edges)

    except Exception as exc:  # noqa: BLE001 — one bad file must not abort the run
        logger.debug("skip %s: unexpected error: %s", path, exc)
        return None


def walk_project(root: Path) -> list[Path]:
    """Walk root recursively, skipping ignored dirs, returning indexable files.

    Rules:
      - Skip any directory whose name starts with '.' (hidden dirs).
      - Skip any directory in SKIP_DIRS.
      - Collect files whose suffix is in config.SEAM_LANGUAGE_MAP.

    The dot/skip check uses only the parts BELOW root (root may itself sit under
    a dot-dir, e.g. ~/.config/proj — that must not exclude everything).
    """
    files: list[Path] = []
    root_depth = len(root.parts)
    for item in root.rglob("*"):
        if any(
            part.startswith(".") or part in SKIP_DIRS
            for part in item.parts[root_depth:]
        ):
            continue
        if item.is_file() and item.suffix.lower() in config.SEAM_LANGUAGE_MAP:
            files.append(item)
    return files
