"""Direct evidence helpers for context-pack output.

This module reads only the existing edges table. It does not infer new
relationships, traverse transitively, or fetch source text; its job is to attach
bounded proof records for the direct caller/callee claims already made by the
pack.
"""

from __future__ import annotations

import sqlite3
from typing import TypedDict

from seam.query.names import edge_match_names


class RelationshipEvidence(TypedDict):
    """One direct edge supporting a context-pack relationship."""

    source: str
    target: str
    direction: str
    kind: str
    file: str
    line: int
    confidence: str
    receiver: str | None
    synthesized_by: str | None
    provenance: str | None


class RelationshipEvidenceTruncated(TypedDict):
    """Counts of direct relationship records dropped by caps."""

    callers: int
    callees: int


class RelationshipEvidenceGroup(TypedDict):
    """Direct inbound and outbound edge evidence for one pack target."""

    callers: list[RelationshipEvidence]
    callees: list[RelationshipEvidence]
    truncated: RelationshipEvidenceTruncated


def _column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    except sqlite3.Error:
        return set()


def _optional_edge_expr(edge_columns: set[str], column: str) -> str:
    return f"e.{column}" if column in edge_columns else "NULL"


def _fetch_direction(
    conn: sqlite3.Connection,
    match_names: list[str],
    *,
    direction: str,
    limit: int,
    edge_columns: set[str],
) -> tuple[list[RelationshipEvidence], int]:
    if not match_names or limit <= 0:
        return [], 0

    placeholders = ",".join("?" * len(match_names))
    receiver_expr = _optional_edge_expr(edge_columns, "receiver")
    synthesized_expr = _optional_edge_expr(edge_columns, "synthesized_by")
    provenance_expr = _optional_edge_expr(edge_columns, "provenance")

    if direction == "incoming":
        where = f"e.target_name IN ({placeholders})"
        order = "e.source_name, e.kind, f.path, e.line, e.id"
    else:
        where = f"e.source_name IN ({placeholders})"
        order = "e.target_name, e.kind, f.path, e.line, e.id"

    rows = conn.execute(
        f"""
        SELECT
            e.source_name,
            e.target_name,
            e.kind,
            f.path AS file,
            e.line,
            e.confidence,
            {receiver_expr} AS receiver,
            {synthesized_expr} AS synthesized_by,
            {provenance_expr} AS provenance
        FROM edges e
        JOIN files f ON f.id = e.file_id
        WHERE {where}
          AND e.source_name != e.target_name
        ORDER BY {order}
        LIMIT ?
        """,
        [*match_names, limit + 1],
    ).fetchall()

    truncated = max(0, len(rows) - limit)
    return [
        RelationshipEvidence(
            source=row["source_name"],
            target=row["target_name"],
            direction=direction,
            kind=row["kind"],
            file=row["file"],
            line=row["line"],
            confidence=row["confidence"],
            receiver=row["receiver"],
            synthesized_by=row["synthesized_by"],
            provenance=row["provenance"],
        )
        for row in rows[:limit]
    ], truncated


def relationship_evidence(
    conn: sqlite3.Connection,
    symbol_name: str,
    *,
    limit: int,
) -> RelationshipEvidenceGroup:
    """Return bounded direct edge evidence around ``symbol_name``.

    ``edge_match_names`` mirrors the existing context semantics, so qualified
    symbols still find bare call edges and class-like containers can include
    member edges. The result is split by direction to match the released pack
    caller/callee vocabulary.
    """
    try:
        match_names = edge_match_names(conn, symbol_name)
    except Exception:  # noqa: BLE001 - pack evidence is best-effort.
        match_names = [symbol_name]

    edge_columns = _column_names(conn, "edges")
    callers, callers_truncated = _fetch_direction(
        conn,
        match_names,
        direction="incoming",
        limit=limit,
        edge_columns=edge_columns,
    )
    callees, callees_truncated = _fetch_direction(
        conn,
        match_names,
        direction="outgoing",
        limit=limit,
        edge_columns=edge_columns,
    )
    return RelationshipEvidenceGroup(
        callers=callers,
        callees=callees,
        truncated=RelationshipEvidenceTruncated(
            callers=callers_truncated,
            callees=callees_truncated,
        ),
    )
