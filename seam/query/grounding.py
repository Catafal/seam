"""Read-only docs/spec grounding query surface."""

from __future__ import annotations

import fnmatch
import sqlite3
from pathlib import Path
from typing import Any, Literal, TypedDict

import seam.config as config

GroundingTargetKind = Literal["symbol", "file", "route", "config", "resource", "doc"]


class GroundingResult(TypedDict):
    found: bool
    query: dict[str, Any]
    candidates: list[dict[str, Any]]
    summary: dict[str, Any]
    caveats: list[str]
    warnings: list[dict[str, str]]
    recommended_next_calls: list[dict[str, Any]]
    omitted: dict[str, int]


_CONFIDENCE_RANK = {"EXACT": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (table,),
    ).fetchone()
    return row is not None


def has_grounding_tables(conn: sqlite3.Connection) -> bool:
    return all(
        _table_exists(conn, table)
        for table in ("document_files", "document_anchors", "document_references")
    )


def query_grounding(
    conn: sqlite3.Connection,
    root: Path,
    *,
    symbol: str | None = None,
    file: str | None = None,
    route: str | None = None,
    config_key: str | None = None,
    resource: str | None = None,
    doc_path: str | None = None,
    query: str | None = None,
    doc_kind: str | None = None,
    status: str | None = None,
    relation_type: str | None = None,
    limit: int | None = None,
    include_snippets: bool = False,
) -> GroundingResult | dict[str, Any]:
    """Return bounded document grounding candidates.

    The result is conservative: document references are intent/provenance evidence,
    not proof that code conforms to a spec or that a dependency exists.
    """
    effective_limit = config.SEAM_GROUNDING_DEFAULT_LIMIT if limit is None else max(0, limit)
    query_payload = {
        "symbol": symbol,
        "file": file,
        "route": route,
        "config_key": config_key,
        "resource": resource,
        "doc_path": doc_path,
        "query": query,
        "doc_kind": doc_kind,
        "status": status,
        "relation_type": relation_type,
        "limit": effective_limit,
        "include_snippets": include_snippets,
    }

    if not has_grounding_tables(conn):
        return {
            "found": False,
            "query": query_payload,
            "candidates": [],
            "summary": {"total": 0, "returned": 0, "omitted": 0},
            "caveats": ["Document grounding requires a v16 index; run seam init or seam sync."],
            "warnings": [
                {
                    "code": "UNSUPPORTED",
                    "message": "Document grounding tables are absent from this index.",
                    "hint": "Run 'seam init' after upgrading Seam.",
                }
            ],
            "recommended_next_calls": [{"tool": "rg", "reason": "Fallback to local text search."}],
            "omitted": {"candidates": 0},
        }

    target_kind, target_values = _target_values(conn, root, symbol, file, route, config_key, resource)
    rows = _fetch_rows(
        conn,
        target_kind=target_kind,
        target_values=target_values,
        doc_path=doc_path,
        query=query,
        doc_kind=doc_kind,
        status=status,
        relation_type=relation_type,
    )
    rows = sorted(rows, key=_sort_key)
    total = len(rows)
    selected = rows[:effective_limit]
    candidates = [_candidate_from_row(root, row, include_snippets=include_snippets) for row in selected]
    summary = _summary(rows, candidates)
    caveats = [
        "Document grounding is explicit local documentation evidence, not runtime or dependency proof.",
        "Low-confidence references are textual leads and should be verified before relying on them.",
    ]
    return {
        "found": bool(candidates),
        "query": query_payload,
        "candidates": candidates,
        "summary": summary,
        "caveats": caveats,
        "warnings": [],
        "recommended_next_calls": _next_calls(candidates, symbol=symbol, file=file),
        "omitted": {"candidates": max(0, total - len(candidates))},
    }


def _target_values(
    conn: sqlite3.Connection,
    root: Path,
    symbol: str | None,
    file: str | None,
    route: str | None,
    config_key: str | None,
    resource: str | None,
) -> tuple[str | None, list[str]]:
    supplied = [
        ("symbol", symbol),
        ("file", file),
        ("route", route),
        ("config", config_key),
        ("resource", resource),
    ]
    present = [(kind, value) for kind, value in supplied if value]
    if not present:
        return None, []
    kind, value = present[0]
    assert value is not None
    if kind == "symbol":
        values = {value}
        rows = conn.execute(
            "SELECT name, qualified_name FROM symbols WHERE name=? OR qualified_name=?",
            (value, value),
        ).fetchall()
        for row in rows:
            values.add(row["name"])
            if row["qualified_name"]:
                values.add(row["qualified_name"])
        return kind, sorted(values)
    if kind == "file":
        path = Path(value)
        rel = path.as_posix()
        if path.is_absolute():
            try:
                rel = path.resolve().relative_to(root).as_posix()
            except ValueError:
                rel = path.as_posix()
        return kind, [rel, str((root / rel).resolve())]
    if kind == "config":
        return kind, [value, value.lower().replace("_", ".")]
    return kind, [value]


def _fetch_rows(
    conn: sqlite3.Connection,
    *,
    target_kind: str | None,
    target_values: list[str],
    doc_path: str | None,
    query: str | None,
    doc_kind: str | None,
    status: str | None,
    relation_type: str | None,
) -> list[sqlite3.Row]:
    where: list[str] = []
    params: list[Any] = []
    if target_kind and target_values:
        placeholders = ",".join("?" for _ in target_values)
        where.append(
            f"""
            (
                dr.target_kind = ?
                AND (
                    dr.target_value IN ({placeholders})
                    OR dr.resolved_value IN ({placeholders})
                )
            )
            """
        )
        params.extend([target_kind, *target_values, *target_values])
    if query:
        pattern = f"%{query}%"
        where.append(
            "(da.search_text LIKE ? OR df.title LIKE ? OR df.path LIKE ? OR dr.target_value LIKE ?)"
        )
        params.extend([pattern, pattern, pattern, pattern])
    if doc_path:
        where.append("df.path = ?")
        params.append(doc_path)
    if doc_kind:
        where.append("df.doc_kind = ?")
        params.append(doc_kind)
    if status:
        where.append("df.status = ?")
        params.append(status)
    if relation_type:
        where.append("dr.relation_type = ?")
        params.append(relation_type)
    sql = """
        SELECT
            df.path AS doc_path,
            df.doc_kind,
            df.status,
            df.title,
            da.heading_path,
            da.slug,
            da.anchor_type,
            da.start_line,
            da.end_line,
            da.search_text,
            dr.target_kind,
            dr.target_value,
            dr.resolved_kind,
            dr.resolved_value,
            dr.relation_type,
            dr.confidence,
            dr.line,
            dr.provenance,
            dr.caveat
        FROM document_references dr
        JOIN document_anchors da ON da.id = dr.anchor_id
        JOIN document_files df ON df.id = da.document_id
    """
    if where:
        sql += " WHERE " + " AND ".join(where)
    return conn.execute(sql, params).fetchall()


def _sort_key(row: sqlite3.Row) -> tuple[int, int, str, int]:
    return (
        _CONFIDENCE_RANK.get(row["confidence"], 9),
        0 if row["resolved_value"] else 1,
        row["doc_path"],
        int(row["line"]),
    )


def _candidate_from_row(root: Path, row: sqlite3.Row, *, include_snippets: bool) -> dict[str, Any]:
    candidate: dict[str, Any] = {
        "doc_path": row["doc_path"],
        "doc_kind": row["doc_kind"],
        "status": row["status"],
        "title": row["title"],
        "heading_path": row["heading_path"],
        "line_range": {"start": row["start_line"], "end": row["end_line"]},
        "relation_type": row["relation_type"],
        "confidence": row["confidence"],
        "provenance": row["provenance"],
        "target": {
            "kind": row["target_kind"],
            "value": row["target_value"],
            "resolved_kind": row["resolved_kind"],
            "resolved_value": row["resolved_value"],
        },
        "caveats": [row["caveat"]] if row["caveat"] else [],
        "recommended_next_calls": [
            {
                "tool": "seam_grounding",
                "reason": "Inspect more grounding evidence from the same document.",
                "params": {"doc_path": row["doc_path"], "include_snippets": True},
            }
        ],
    }
    if include_snippets:
        candidate["snippet"] = _read_snippet(
            root / row["doc_path"],
            int(row["start_line"]),
            int(row["end_line"]),
        )
    return candidate


def _read_snippet(path: Path, start_line: int, end_line: int) -> str | None:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    selected = "\n".join(lines[max(0, start_line - 1) : max(start_line, end_line)])
    if len(selected.encode("utf-8")) <= config.SEAM_GROUNDING_MAX_SNIPPET_BYTES:
        return selected
    encoded = selected.encode("utf-8")[: config.SEAM_GROUNDING_MAX_SNIPPET_BYTES]
    return encoded.decode("utf-8", errors="ignore") + "\n[truncated]"


def _summary(rows: list[sqlite3.Row], candidates: list[dict[str, Any]]) -> dict[str, Any]:
    by_kind: dict[str, int] = {}
    by_status: dict[str, int] = {}
    by_relation: dict[str, int] = {}
    by_confidence: dict[str, int] = {}
    for row in rows:
        by_kind[row["doc_kind"]] = by_kind.get(row["doc_kind"], 0) + 1
        by_status[row["status"]] = by_status.get(row["status"], 0) + 1
        by_relation[row["relation_type"]] = by_relation.get(row["relation_type"], 0) + 1
        by_confidence[row["confidence"]] = by_confidence.get(row["confidence"], 0) + 1
    return {
        "total": len(rows),
        "returned": len(candidates),
        "omitted": max(0, len(rows) - len(candidates)),
        "by_doc_kind": by_kind,
        "by_status": by_status,
        "by_relation_type": by_relation,
        "by_confidence": by_confidence,
    }


def _next_calls(
    candidates: list[dict[str, Any]],
    *,
    symbol: str | None,
    file: str | None,
) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    if candidates:
        calls.append(
            {
                "tool": "seam_grounding",
                "reason": "Fetch bounded snippets for the returned grounding anchors.",
                "params": {"include_snippets": True},
            }
        )
    if symbol:
        calls.append(
            {
                "tool": "seam_context",
                "reason": "Inspect code relationships separately from doc grounding.",
                "params": {"symbol": symbol},
            }
        )
    if file:
        calls.append(
            {
                "tool": "seam_structure",
                "reason": "Inspect nearby files without treating docs as dependencies.",
                "params": {"path": file},
            }
        )
    return calls


def doc_matches_pattern(path: str, pattern: str | None) -> bool:
    if pattern is None:
        return True
    return fnmatch.fnmatch(path, pattern)
