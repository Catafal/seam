"""Read-only query layer for semantic comments (WHY/HACK/NOTE/TODO/FIXME).

Provides why() — the single entry point for comment lookup by:
  - file path (return all comments in that file)
  - file + line (return comments within ±RADIUS lines)
  - symbol name (return comments in/above the symbol's body)

No server or CLI imports; this module lives at the query layer only.
Callers (handler, CLI) are responsible for path resolution and relativization.
"""

import logging
import sqlite3
from typing import TypedDict

logger = logging.getLogger(__name__)

# ── Constants (documented; not env-driven — fixed marker set decision) ────────

# Proximity radius: when querying by line, return comments within ±RADIUS lines.
# Example: line=20, RADIUS=15 -> window [5, 35].
RADIUS: int = 15

# Lead lines: for symbol lookup, extend the search range this many lines ABOVE
# the symbol's start_line to capture "pre-symbol" rationale comments.
# Example: start_line=10, LEAD=5 -> search from line 5.
LEAD: int = 5


# ── Output TypedDict ──────────────────────────────────────────────────────────


class CommentHit(TypedDict):
    """One semantic comment result returned by why().

    file   — absolute path (DB-stored); handler/CLI relativizes before output.
    line   — 1-based line number in the source file.
    marker — normalized UPPERCASE: WHY | HACK | NOTE | TODO | FIXME.
    text   — comment body after the marker (and optional colon), stripped.
    """

    file: str
    line: int
    marker: str
    text: str


# ── Internal helpers ──────────────────────────────────────────────────────────


def _comments_table_exists(conn: sqlite3.Connection) -> bool:
    """Return True if the comments table exists in this database.

    WHY: connect() (used by `seam start`, `seam status`, `seam why`) opens a bare
    connection and does NOT run the schema script — only init_db() does. An index
    created before this slice (schema v2) therefore has no comments table on those
    connections. Querying it would raise OperationalError, but the MCP/CLI contract
    is an empty list ("no recorded rationale"), not an error. We detect the missing
    table and degrade gracefully, logging a one-time hint to re-index.
    """
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='comments' LIMIT 1"
    ).fetchone()
    return row is not None


def _fetch_by_file_id(
    conn: sqlite3.Connection,
    file_id: int,
    file_path: str,
    low: int | None = None,
    high: int | None = None,
) -> list[CommentHit]:
    """Fetch comments for a file_id, optionally filtering to a line range [low, high].

    Results are sorted by line number (ascending).
    """
    if low is not None and high is not None:
        rows = conn.execute(
            """
            SELECT line, marker, text
            FROM comments
            WHERE file_id = ? AND line BETWEEN ? AND ?
            ORDER BY line
            """,
            (file_id, low, high),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT line, marker, text
            FROM comments
            WHERE file_id = ?
            ORDER BY line
            """,
            (file_id,),
        ).fetchall()

    return [
        CommentHit(file=file_path, line=row["line"], marker=row["marker"], text=row["text"])
        for row in rows
    ]


def _resolve_file_id(conn: sqlite3.Connection, file_path: str) -> tuple[int, str] | None:
    """Look up a file_id + stored path by exact path match.

    Returns (file_id, stored_path) or None if not found.
    The stored_path is what the DB has (resolved absolute path at index time).
    """
    row = conn.execute(
        "SELECT id, path FROM files WHERE path = ?", (file_path,)
    ).fetchone()
    if row is None:
        return None
    return row["id"], row["path"]


def _resolve_symbol(
    conn: sqlite3.Connection, symbol_name: str
) -> tuple[int, str, int, int] | None:
    """Resolve a symbol name to (file_id, file_path, start_line, end_line).

    When multiple symbols share the same name, returns the first by (file path, symbol id)
    — deterministic ordering consistent with engine.context().

    Returns None if the symbol is not in the index.
    """
    row = conn.execute(
        """
        SELECT s.file_id, f.path, s.start_line, s.end_line
        FROM symbols s
        JOIN files f ON f.id = s.file_id
        WHERE s.name = ?
        ORDER BY f.path, s.id
        LIMIT 1
        """,
        (symbol_name,),
    ).fetchone()
    if row is None:
        return None
    return row["file_id"], row["path"], row["start_line"], row["end_line"]


# ── Public API ────────────────────────────────────────────────────────────────


def why(
    conn: sqlite3.Connection,
    *,
    file: str | None = None,
    line: int | None = None,
    symbol: str | None = None,
) -> list[CommentHit]:
    """Return semantic comments near a location or symbol.

    Lookup modes (at least one of file/symbol is required):
      file only        → all comments for that file (exact path match vs DB).
      file + line      → comments within ±RADIUS lines of `line`.
      symbol           → comments in [start_line - LEAD, end_line] of the symbol.

    Args:
        conn:   Open read-only SQLite connection to the Seam index.
        file:   Absolute file path to look up (must match files.path exactly).
        line:   1-based line number (only meaningful with `file`; ignored when
                `symbol` is given — symbol mode uses the symbol's own line range).
        symbol: Symbol name to look up (resolved via symbols table).

    Returns:
        List of CommentHit dicts sorted by line number. Empty list when the
        file/symbol is not indexed or has no semantic comments.

    Raises:
        ValueError: When neither `file` nor `symbol` is provided.

    Note on path matching: The DB stores resolved absolute paths. The caller
    (handler/CLI) must resolve the user-supplied path to an absolute path
    before passing it here. This function does exact string matching on files.path.
    """
    if file is None and symbol is None:
        raise ValueError("why() requires at least one of: file, symbol")

    # Pre-1b indexes (schema v2) opened via connect() have no comments table.
    # Degrade to the empty-list contract instead of raising OperationalError.
    if not _comments_table_exists(conn):
        logger.warning(
            "seam_why: comments table missing (index predates this feature) — "
            "run 'seam init' to enable semantic comments. Returning no results."
        )
        return []

    # Symbol mode: resolve symbol -> file + line range, then query comments
    if symbol is not None:
        resolved = _resolve_symbol(conn, symbol)
        if resolved is None:
            return []
        file_id, file_path, start_line, end_line = resolved
        # Extend the range above the symbol by LEAD lines to capture pre-symbol rationale.
        # Clamp low to 1 so we never search for line < 1.
        low = max(1, start_line - LEAD)
        return _fetch_by_file_id(conn, file_id, file_path, low=low, high=end_line)

    # File-only or file+line mode
    resolved_file = _resolve_file_id(conn, file)  # type: ignore[arg-type]
    if resolved_file is None:
        return []

    file_id, file_path = resolved_file

    if line is not None:
        # Proximity query: [line - RADIUS, line + RADIUS]
        low = max(1, line - RADIUS)
        high = line + RADIUS
        return _fetch_by_file_id(conn, file_id, file_path, low=low, high=high)

    # File-only: all comments for this file
    return _fetch_by_file_id(conn, file_id, file_path)
