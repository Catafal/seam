"""Exact source retrieval for `seam_snippet`.

Owns: resolving one indexed selector into bounded live source text.
Does not own: graph context, semantic search, or re-indexing stale files.
Invariants: indexed paths must stay under the requested root before any source
read; ambiguity returns candidates instead of guessing.

The database chooses identity and line ranges, while the filesystem supplies
live source bytes. Keeping those responsibilities split lets agents ask for an
exact implementation body after search/query without bloating every graph read.
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_MAX_LINES = 200
DEFAULT_MAX_BYTES = 20_000
MAX_CONTEXT_LINES = 20
MAX_LINES_LIMIT = 2_000
MAX_BYTES_LIMIT = 200_000


def _warning(code: str, message: str, hint: str) -> dict[str, str]:
    return {"code": code, "message": message, "hint": hint}


def _compute_uid(file_path: str, start_line: int) -> str:
    """Mirror Seam's public UID contract without changing the critical UID helpers."""
    digest = hashlib.sha1(file_path.encode("utf-8")).hexdigest()[:8]
    return f"{digest}:{start_line}"


def _relativize(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _display_path(path: Path, root: Path) -> str:
    """Return a non-leaking path for payloads that may describe rejected rows."""
    try:
        return str(path.resolve(strict=False).relative_to(root))
    except ValueError:
        try:
            return str(path.relative_to(root))
        except ValueError:
            return f"<outside-root>/{path.name}"


def _resolve_uid_row(conn: sqlite3.Connection, uid: str) -> sqlite3.Row | None:
    prefix, sep, line_str = uid.partition(":")
    if not sep or len(prefix) != 8 or not line_str.isdigit():
        return None
    start_line = int(line_str)
    rows = conn.execute(
        """
        SELECT
            s.name,
            s.kind,
            s.start_line,
            s.end_line,
            s.docstring,
            s.signature,
            f.path AS file,
            f.file_hash,
            f.mtime
        FROM symbols s
        JOIN files f ON f.id = s.file_id
        WHERE s.start_line = ?
        ORDER BY s.id
        """,
        (start_line,),
    ).fetchall()
    for row in rows:
        if _compute_uid(row["file"], start_line) == uid:
            return row
    return None


def _fetch_symbol_rows(
    conn: sqlite3.Connection,
    *,
    symbol: str,
    file: str | None,
    root: Path,
) -> list[sqlite3.Row]:
    params: list[Any] = [symbol]
    file_clause = ""
    if file:
        wanted = Path(file)
        wanted_abs = wanted if wanted.is_absolute() else root / wanted
        file_clause = " AND f.path = ?"
        params.append(str(wanted_abs.resolve(strict=False)))
    return conn.execute(
        f"""
        SELECT
            s.name,
            s.kind,
            s.start_line,
            s.end_line,
            s.docstring,
            s.signature,
            f.path AS file,
            f.file_hash,
            f.mtime
        FROM symbols s
        JOIN files f ON f.id = s.file_id
        WHERE s.name = ?{file_clause}
        ORDER BY s.id
        """,
        params,
    ).fetchall()


def _fetch_location_rows(
    conn: sqlite3.Connection,
    *,
    file: str,
    line: int,
    root: Path,
) -> list[sqlite3.Row]:
    wanted = Path(file)
    wanted_abs = wanted if wanted.is_absolute() else root / wanted
    return conn.execute(
        """
        SELECT
            s.name,
            s.kind,
            s.start_line,
            s.end_line,
            s.docstring,
            s.signature,
            f.path AS file,
            f.file_hash,
            f.mtime
        FROM symbols s
        JOIN files f ON f.id = s.file_id
        WHERE f.path = ?
          AND s.start_line <= ?
          AND s.end_line >= ?
        ORDER BY (s.end_line - s.start_line) ASC, s.id ASC
        """,
        (str(wanted_abs.resolve(strict=False)), line, line),
    ).fetchall()


def _nearby_candidates(
    conn: sqlite3.Connection,
    *,
    file: str,
    line: int,
    root: Path,
    limit: int = 5,
) -> list[dict[str, Any]]:
    wanted = Path(file)
    wanted_abs = wanted if wanted.is_absolute() else root / wanted
    rows = conn.execute(
        """
        SELECT
            s.name,
            s.kind,
            s.start_line,
            s.end_line,
            s.signature,
            f.path AS file
        FROM symbols s
        JOIN files f ON f.id = s.file_id
        WHERE f.path = ?
        ORDER BY ABS(s.start_line - ?), s.id
        LIMIT ?
        """,
        (str(wanted_abs.resolve(strict=False)), line, limit),
    ).fetchall()
    return [_candidate(row, root=root) for row in rows]


def _same_file_neighbors(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    *,
    root: Path,
) -> list[dict[str, Any]]:
    """Return adjacent indexed symbols as navigation hints, not expanded context."""
    rows: list[sqlite3.Row] = []
    previous = conn.execute(
        """
        SELECT
            s.name,
            s.kind,
            s.start_line,
            s.end_line,
            s.signature,
            f.path AS file
        FROM symbols s
        JOIN files f ON f.id = s.file_id
        WHERE f.path = ?
          AND s.start_line < ?
        ORDER BY s.start_line DESC, s.id DESC
        LIMIT 1
        """,
        (row["file"], row["start_line"]),
    ).fetchone()
    if previous is not None:
        rows.append(previous)

    next_row = conn.execute(
        """
        SELECT
            s.name,
            s.kind,
            s.start_line,
            s.end_line,
            s.signature,
            f.path AS file
        FROM symbols s
        JOIN files f ON f.id = s.file_id
        WHERE f.path = ?
          AND s.start_line > ?
        ORDER BY s.start_line ASC, s.id ASC
        LIMIT 1
        """,
        (row["file"], row["start_line"]),
    ).fetchone()
    if next_row is not None:
        rows.append(next_row)

    return [_candidate(candidate_row, root=root) for candidate_row in rows]


def _candidate(row: sqlite3.Row, *, root: Path) -> dict[str, Any]:
    return {
        "symbol": row["name"],
        "uid": _compute_uid(row["file"], row["start_line"]),
        "kind": row["kind"],
        "file": _display_path(Path(row["file"]), root),
        "start_line": row["start_line"],
        "end_line": row["end_line"],
        "signature": row["signature"],
    }


def _ambiguity(rows: list[sqlite3.Row], *, root: Path) -> dict[str, Any]:
    message = "Multiple indexed symbols match this selector."
    return {
        "found": False,
        "ambiguous": True,
        "reason": "AMBIGUOUS_SYMBOL",
        "message": message,
        "candidates": [_candidate(row, root=root) for row in rows],
        "warnings": [_warning(
            "AMBIGUOUS_SYMBOL",
            message,
            "Pass one candidate uid in the next seam_snippet call.",
        )],
    }


def _not_found(reason: str, message: str, hint: str) -> dict[str, Any]:
    return {
        "found": False,
        "reason": reason,
        "message": message,
        "candidates": [],
        "warnings": [_warning(reason, message, hint)],
    }


def _clamp_nonnegative(value: int, upper: int) -> int:
    return max(0, min(value, upper))


def _clamp_positive(value: int, upper: int) -> int:
    return max(1, min(value, upper))


def _read_source_window(
    path: Path,
    *,
    source_start: int,
    source_end: int,
    max_lines: int,
    max_bytes: int,
) -> tuple[str, str, int, int, bool, bool, list[dict[str, str]]]:
    digest = hashlib.sha1()  # noqa: S324 - matches Seam's non-security file hash.
    warnings: list[dict[str, str]] = []
    collected = bytearray()
    returned_lines = 0
    total_lines = 0
    by_lines = False
    by_bytes = False
    decode_replaced = False

    with path.open("rb") as handle:
        for line_no, raw_line in enumerate(handle, start=1):
            digest.update(raw_line)
            total_lines = line_no
            if line_no < source_start or line_no > source_end:
                continue
            if returned_lines >= max_lines:
                by_lines = True
                continue
            remaining_bytes = max_bytes - len(collected)
            if remaining_bytes <= 0:
                by_bytes = True
                continue
            candidate = raw_line
            if len(candidate) > remaining_bytes:
                candidate = candidate[:remaining_bytes]
                by_bytes = True
            collected.extend(candidate)
            returned_lines += 1
            try:
                candidate.decode("utf-8")
            except UnicodeDecodeError:
                decode_replaced = True

    if decode_replaced:
        warnings.append(_warning(
            "SOURCE_DECODE_REPLACED",
            "Returned source bytes were not valid UTF-8; invalid bytes were replaced.",
            "Run a fresh index after normalizing the file encoding if exact bytes matter.",
        ))
    source = bytes(collected).decode("utf-8", errors="replace")
    return (
        source,
        digest.hexdigest(),
        total_lines,
        returned_lines,
        by_lines,
        by_bytes,
        warnings,
    )


def _source_error_payload(
    row: sqlite3.Row,
    *,
    root: Path,
    code: str,
    message: str,
    hint: str,
) -> dict[str, Any]:
    abs_file = Path(row["file"])
    return {
        "found": False,
        "symbol": row["name"],
        "uid": _compute_uid(row["file"], row["start_line"]),
        "kind": row["kind"],
        "file": _display_path(abs_file, root),
        "start_line": row["start_line"],
        "end_line": row["end_line"],
        "reason": code,
        "message": message,
        "candidates": [],
        "warnings": [_warning(code, message, hint)],
    }


def snippet(
    conn: sqlite3.Connection,
    *,
    root: Path,
    uid: str | None = None,
    symbol: str | None = None,
    file: str | None = None,
    line: int | None = None,
    context_lines: int = 0,
    max_lines: int = DEFAULT_MAX_LINES,
    max_bytes: int = DEFAULT_MAX_BYTES,
    include_neighbors: bool = False,
) -> dict[str, Any]:
    """Apply one selector contract so CLI, MCP, and web never diverge on source reads."""
    has_uid = bool(uid and uid.strip())
    has_symbol = bool(symbol and symbol.strip())
    has_file = bool(file and file.strip())
    has_line = line is not None
    has_uid_selector = has_uid and not has_symbol and not has_file and not has_line
    has_symbol_selector = has_symbol and not has_uid and not has_line
    has_location_selector = has_file and has_line and not has_uid and not has_symbol
    selector_count = sum([has_uid_selector, has_symbol_selector, has_location_selector])
    if selector_count != 1:
        return {
            "error": "INVALID_INPUT",
            "message": "provide exactly one selector: uid, symbol, symbol+file, or file+line",
        }

    root_resolved = root.resolve()
    row: sqlite3.Row | None
    if has_uid_selector and uid is not None:
        row = _resolve_uid_row(conn, uid.strip())
        if row is None:
            return _not_found(
                "UNKNOWN_UID",
                "No indexed symbol matches this uid.",
                "Run seam_search or seam_query again and pass one of the returned uid values.",
            )
    elif has_symbol and symbol is not None:
        rows = _fetch_symbol_rows(conn, symbol=symbol.strip(), file=file, root=root_resolved)
        if not rows:
            return _not_found(
                "UNKNOWN_SYMBOL",
                "No indexed symbol matches this selector.",
                "Run seam_search or seam_query to discover available symbols.",
            )
        if len(rows) > 1:
            return _ambiguity(rows, root=root_resolved)
        row = rows[0]
    elif has_file and line is not None and file is not None:
        rows = _fetch_location_rows(conn, file=file, line=line, root=root_resolved)
        if not rows:
            message = "No indexed symbol contains this file and line."
            return {
                "found": False,
                "reason": "NO_SYMBOL_AT_LOCATION",
                "message": message,
                "candidates": _nearby_candidates(
                    conn,
                    file=file,
                    line=line,
                    root=root_resolved,
                ),
                "warnings": [_warning(
                    "NO_SYMBOL_AT_LOCATION",
                    message,
                    "Use one nearby candidate uid or choose a line inside a symbol range.",
                )],
            }
        narrowest_span = rows[0]["end_line"] - rows[0]["start_line"]
        best_rows = [r for r in rows if r["end_line"] - r["start_line"] == narrowest_span]
        if len(best_rows) > 1:
            return _ambiguity(best_rows, root=root_resolved)
        row = best_rows[0]
    else:
        row = None

    if row is None:
        return {"error": "INVALID_INPUT", "message": "invalid seam_snippet selector"}

    raw_file = Path(row["file"])
    if str(raw_file).startswith(":"):
        return _source_error_payload(
            row,
            root=root_resolved,
            code="SOURCE_NOT_REGULAR_FILE",
            message="The indexed row points at an internal Seam file marker.",
            hint="Use a real source symbol from seam_search or seam_query.",
        )
    try:
        resolved_file = raw_file.resolve(strict=False)
        resolved_file.relative_to(root_resolved)
    except ValueError:
        return _source_error_payload(
            row,
            root=root_resolved,
            code="SOURCE_OUTSIDE_ROOT",
            message="The indexed source path resolves outside the project root.",
            hint="Rebuild the index from the intended project root.",
        )

    if not resolved_file.exists():
        return _source_error_payload(
            row,
            root=root_resolved,
            code="SOURCE_FILE_MISSING",
            message="The indexed source file no longer exists on disk.",
            hint="Run seam sync or seam init to refresh the index.",
        )
    if not resolved_file.is_file():
        return _source_error_payload(
            row,
            root=root_resolved,
            code="SOURCE_NOT_REGULAR_FILE",
            message="The indexed source path is not a regular file.",
            hint="Run seam sync or seam init after removing or replacing the path.",
        )

    warnings: list[dict[str, str]] = []
    context = _clamp_nonnegative(context_lines, MAX_CONTEXT_LINES)
    safe_max_lines = _clamp_positive(max_lines, MAX_LINES_LIMIT)
    safe_max_bytes = _clamp_positive(max_bytes, MAX_BYTES_LIMIT)

    start_line = int(row["start_line"])
    end_line = int(row["end_line"])
    source_start = max(1, start_line - context)
    requested_source_end = end_line + context

    try:
        (
            source,
            current_hash,
            total_lines,
            returned_line_count,
            by_lines,
            by_bytes,
            decode_warnings,
        ) = _read_source_window(
            resolved_file,
            source_start=source_start,
            source_end=requested_source_end,
            max_lines=safe_max_lines,
            max_bytes=safe_max_bytes,
        )
        warnings.extend(decode_warnings)
        stat = resolved_file.stat()
    except OSError as exc:
        logger.warning("snippet: failed to read %s: %s", resolved_file, exc)
        return _source_error_payload(
            row,
            root=root_resolved,
            code="SOURCE_READ_ERROR",
            message="Failed to read indexed source file.",
            hint="Check file permissions and retry.",
        )

    file_hash_matches = current_hash == row["file_hash"]
    mtime_matches = abs(float(stat.st_mtime) - float(row["mtime"])) < 1e-6
    if not file_hash_matches or not mtime_matches:
        warnings.append(_warning(
            "SOURCE_MAY_BE_STALE",
            "The live source file differs from the indexed file metadata.",
            "Run seam sync before trusting indexed line ranges.",
        ))

    source_end = min(total_lines, requested_source_end)
    if end_line > total_lines:
        warnings.append(_warning(
            "SOURCE_RANGE_PAST_EOF",
            "The indexed symbol range extends past the current end of file.",
            "Run seam sync to refresh symbol ranges.",
        ))
    original_line_count = max(0, source_end - source_start + 1)
    if by_lines:
        warnings.append(_warning(
            "SNIPPET_TRUNCATED_LINES",
            "Snippet exceeded the requested line cap.",
            "Increase max_lines for a larger deliberate source read.",
        ))
    if by_bytes:
        warnings.append(_warning(
            "SNIPPET_TRUNCATED_BYTES",
            "Snippet exceeded the requested byte cap.",
            "Increase max_bytes for a larger deliberate source read.",
        ))
    if returned_line_count:
        source_end = source_start + returned_line_count - 1
    else:
        source_end = source_start - 1

    return {
        "found": True,
        "symbol": row["name"],
        "uid": _compute_uid(row["file"], start_line),
        "kind": row["kind"],
        "file": _relativize(resolved_file, root_resolved),
        "start_line": start_line,
        "end_line": end_line,
        "source_start_line": source_start,
        "source_end_line": source_end,
        "signature": row["signature"],
        "docstring": row["docstring"],
        "source": source,
        "truncated": {
            "by_lines": by_lines,
            "by_bytes": by_bytes,
            "original_line_count": original_line_count,
            "returned_line_count": returned_line_count,
        },
        "freshness": {
            "file_hash_matches": file_hash_matches,
            "mtime_matches": mtime_matches,
            "index_stale": not file_hash_matches or not mtime_matches,
        },
        "neighbors": _same_file_neighbors(conn, row, root=root_resolved)
        if include_neighbors
        else [],
        "warnings": warnings,
    }
