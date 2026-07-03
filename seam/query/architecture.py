"""Read-only repository architecture briefing for `seam_architecture`.

This module composes existing index evidence into a compact repo briefing. It is
transport-neutral by design: CLI, MCP, and Web adapters should only validate
arguments and pass through this payload.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any

import seam
from seam.analysis.staleness import check_staleness
from seam.analysis.testpaths import is_test_file
from seam.query.structure import build_structure

_OPTIONAL_SURFACES: dict[str, dict[str, str]] = {
    "routes": {
        "code": "NO_ROUTE_EDGES",
        "message": "Route edges are not supported by the current Seam graph schema.",
        "hint": "Route summaries will populate after a future route-edge extraction phase.",
    },
    "configs": {
        "code": "NO_CONFIG_EDGES",
        "message": "Config/resource edges are not supported by the current Seam graph schema.",
        "hint": "Config summaries will populate after a future config/resource extraction phase.",
    },
    "resources": {
        "code": "NO_RESOURCE_EDGES",
        "message": "Resource edges are not supported by the current Seam graph schema.",
        "hint": "Resource summaries will populate after a future resource extraction phase.",
    },
    "test_edges": {
        "code": "NO_TEST_EDGES",
        "message": "Explicit test coverage edges are not supported by the current Seam graph schema.",
        "hint": "Test summaries currently use path heuristics, not coverage edges.",
    },
    "exceptions": {
        "code": "NO_EXCEPTION_EDGES",
        "message": "Exception edges are not populated in the current Seam graph.",
        "hint": "Run 'seam sync' or 'seam init' with P3.4 support to populate raises/catches edges.",
    },
}
_INFRA_RESOURCE_CATEGORIES = {
    "service",
    "image",
    "dockerfile",
    "build_context",
    "port",
    "stage",
    "env_file",
    "volume",
    "network",
}

_DEFAULT_SECTIONS: tuple[str, ...] = (
    "summary",
    "languages",
    "physical",
    "clusters",
    "entry_points",
    "routes",
    "http_calls",
    "configs",
    "resources",
    "infra",
    "exceptions",
    "hotspots",
    "orchestrators",
    "boundaries",
    "edge_mix",
    "tests",
    "optional_surfaces",
)
_SECTION_ORDER: dict[str, int] = {section: index for index, section in enumerate(_DEFAULT_SECTIONS)}


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name = ? AND type IN ('table', 'view')",
        (table,),
    ).fetchone()
    return row is not None


def _column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    if not _table_exists(conn, table):
        return set()
    try:
        return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    except sqlite3.Error:
        return set()


def _metadata(conn: sqlite3.Connection) -> dict[str, str]:
    if not _table_exists(conn, "metadata"):
        return {}
    try:
        rows = conn.execute("SELECT key, value FROM metadata ORDER BY key").fetchall()
    except sqlite3.Error:
        return {}
    return {str(row["key"]): str(row["value"]) for row in rows}


def _compute_uid(file_path: str, start_line: int) -> str:
    digest = hashlib.sha1(file_path.encode("utf-8")).hexdigest()[:8]
    return f"{digest}:{start_line}"


def _relativize(path: str, root: Path) -> str:
    try:
        return str(Path(path).resolve(strict=False).relative_to(root))
    except ValueError:
        return f"<outside-root>/{Path(path).name}"


def _area_for_file(abs_file: str, root: Path) -> str:
    rel = _relativize(abs_file, root)
    if rel.startswith("<outside-root>/"):
        return rel
    first, _, _rest = rel.partition("/")
    return first or "."


def _count(conn: sqlite3.Connection, table: str, where: str = "") -> int:
    if not _table_exists(conn, table):
        return 0
    try:
        sql = f"SELECT COUNT(*) FROM {table}"
        if where:
            sql += f" WHERE {where}"
        return int(conn.execute(sql).fetchone()[0])
    except sqlite3.Error:
        return 0


def _group_counts(
    conn: sqlite3.Connection, table: str, column: str, where: str = ""
) -> dict[str, int]:
    if column not in _column_names(conn, table):
        return {}
    try:
        sql = f"SELECT {column} AS key, COUNT(*) AS count FROM {table}"
        if where:
            sql += f" WHERE {where}"
        sql += f" GROUP BY {column} ORDER BY {column}"
        rows = conn.execute(sql).fetchall()
    except sqlite3.Error:
        return {}
    return {str(row["key"]): int(row["count"]) for row in rows if row["key"] is not None}


def _schema_version(meta: dict[str, str]) -> int | str:
    raw = meta.get("schema_version", "0")
    try:
        return int(raw)
    except ValueError:
        return raw


def _file_rows(
    conn: sqlite3.Connection, allowed_files: set[str] | None = None
) -> list[sqlite3.Row]:
    if not _table_exists(conn, "files"):
        return []
    try:
        rows = conn.execute(
            "SELECT path, language FROM files WHERE path NOT LIKE ':%' ORDER BY path"
        ).fetchall()
    except sqlite3.Error:
        return []
    if allowed_files is not None:
        rows = [row for row in rows if str(row["path"]) in allowed_files]
    return rows


def _symbol_counts_by_language(
    conn: sqlite3.Connection,
    allowed_files: set[str] | None = None,
) -> dict[str, int]:
    if not _table_exists(conn, "symbols") or not _table_exists(conn, "files"):
        return {}
    if "language" not in _column_names(conn, "files"):
        return {}
    result: dict[str, int] = defaultdict(int)
    try:
        rows = conn.execute(
            """
            SELECT f.path AS file, f.language AS language, COUNT(s.id) AS count
            FROM files f
            LEFT JOIN symbols s ON s.file_id = f.id
            WHERE f.path NOT LIKE ':%'
            GROUP BY f.path, f.language
            """
        ).fetchall()
    except sqlite3.Error:
        rows = []
    for row in rows:
        if allowed_files is not None and str(row["file"]) not in allowed_files:
            continue
        if row["language"] is not None:
            result[str(row["language"])] += int(row["count"])
    return dict(result)


def _language_section(
    conn: sqlite3.Connection, allowed_files: set[str] | None = None
) -> dict[str, Any]:
    file_counts: dict[str, int] = defaultdict(int)
    for row in _file_rows(conn, allowed_files):
        if row["language"] is not None:
            file_counts[str(row["language"])] += 1
    symbol_counts = _symbol_counts_by_language(conn, allowed_files)
    languages = sorted(set(file_counts) | set(symbol_counts))
    return {
        "items": [
            {
                "language": language,
                "files": file_counts.get(language, 0),
                "symbols": symbol_counts.get(language, 0),
            }
            for language in languages
        ],
        "truncated": 0,
    }


def _test_file_counts(files: list[sqlite3.Row]) -> tuple[int, int]:
    test_files = sum(1 for row in files if is_test_file(str(row["path"])))
    production_files = len(files) - test_files
    return test_files, production_files


def _count_joined(
    conn: sqlite3.Connection,
    table: str,
    allowed_files: set[str] | None,
) -> int:
    if allowed_files is None:
        return _count(conn, table)
    if not allowed_files:
        return 0
    if not _table_exists(conn, table) or not _table_exists(conn, "files"):
        return 0
    try:
        rows = conn.execute(
            f"""
            SELECT f.path AS file, COUNT(t.id) AS count
            FROM files f
            LEFT JOIN {table} t ON t.file_id = f.id
            WHERE f.path NOT LIKE ':%'
            GROUP BY f.path
            """
        ).fetchall()
    except sqlite3.Error:
        return 0
    return sum(int(row["count"]) for row in rows if str(row["file"]) in allowed_files)


def _physical_section(
    conn: sqlite3.Connection,
    root: Path,
    allowed_files: set[str] | None = None,
    scope_path: Path | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    areas: dict[str, dict[str, Any]] = {}
    files = _file_rows(conn, allowed_files)
    for row in files:
        area = _area_for_file(str(row["path"]), root)
        bucket = areas.setdefault(area, {"path": area, "files": 0, "symbols": 0, "edges": 0})
        bucket["files"] += 1

    if _table_exists(conn, "symbols") and _table_exists(conn, "files"):
        try:
            rows = conn.execute(
                """
                SELECT f.path AS file, COUNT(s.id) AS count
                FROM files f
                LEFT JOIN symbols s ON s.file_id = f.id
                WHERE f.path NOT LIKE ':%'
                GROUP BY f.path
                """
            ).fetchall()
        except sqlite3.Error:
            rows = []
        for row in rows:
            if allowed_files is not None and str(row["file"]) not in allowed_files:
                continue
            area = _area_for_file(str(row["file"]), root)
            bucket = areas.setdefault(area, {"path": area, "files": 0, "symbols": 0, "edges": 0})
            bucket["symbols"] += int(row["count"])

    if _table_exists(conn, "edges") and _table_exists(conn, "files"):
        try:
            rows = conn.execute(
                """
                SELECT f.path AS file, COUNT(e.id) AS count
                FROM files f
                LEFT JOIN edges e ON e.file_id = f.id
                WHERE f.path NOT LIKE ':%'
                GROUP BY f.path
                """
            ).fetchall()
        except sqlite3.Error:
            rows = []
        for row in rows:
            if allowed_files is not None and str(row["file"]) not in allowed_files:
                continue
            area = _area_for_file(str(row["file"]), root)
            bucket = areas.setdefault(area, {"path": area, "files": 0, "symbols": 0, "edges": 0})
            bucket["edges"] += int(row["count"])

    top_areas = sorted(
        areas.values(),
        key=lambda item: (-int(item["symbols"]), -int(item["edges"]), str(item["path"])),
    )
    return {
        "top_areas": top_areas[:limit],
        "structure": build_structure(conn, root, path=scope_path, max_depth=4, max_nodes=80),
        "truncated": max(0, len(top_areas) - limit),
    }


def _cluster_section(
    conn: sqlite3.Connection,
    root: Path,
    allowed_files: set[str] | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    if not _table_exists(conn, "clusters") or "cluster_id" not in _column_names(conn, "symbols"):
        return {"items": [], "truncated": 0}
    try:
        rows = conn.execute(
            """
            SELECT
                c.id AS cluster_id,
                c.label AS label,
                c.size AS size,
                s.name AS symbol,
                s.kind AS kind,
                s.start_line AS line,
                f.path AS file
            FROM clusters c
            LEFT JOIN symbols s ON s.cluster_id = c.id
            LEFT JOIN files f ON f.id = s.file_id
            ORDER BY c.size DESC, c.id, f.path, s.start_line, s.name
            """
        ).fetchall()
    except sqlite3.Error:
        return {"items": [], "truncated": 0}

    grouped: dict[int, dict[str, Any]] = {}
    area_counts: dict[int, dict[str, int]] = defaultdict(dict)
    for row in rows:
        if (
            row["file"] is not None
            and allowed_files is not None
            and str(row["file"]) not in allowed_files
        ):
            continue
        cluster_id = int(row["cluster_id"])
        grouped.setdefault(
            cluster_id,
            {
                "cluster_id": cluster_id,
                "label": row["label"],
                "size": 0 if allowed_files is not None else int(row["size"]),
                "representative": None,
                "top_physical_areas": [],
            },
        )
        if row["symbol"] is None or row["file"] is None:
            continue
        if allowed_files is not None:
            grouped[cluster_id]["size"] += 1
        if grouped[cluster_id]["representative"] is None:
            grouped[cluster_id]["representative"] = {
                "symbol": row["symbol"],
                "uid": _compute_uid(row["file"], int(row["line"])),
                "kind": row["kind"],
                "file": _relativize(row["file"], root),
                "line": int(row["line"]),
            }
        area = _area_for_file(row["file"], root)
        area_counts[cluster_id][area] = area_counts[cluster_id].get(area, 0) + 1

    items = list(grouped.values())
    for item in items:
        cluster_areas = area_counts.get(int(item["cluster_id"]), {})
        item["top_physical_areas"] = [
            {"path": path, "symbols": count}
            for path, count in sorted(cluster_areas.items(), key=lambda pair: (-pair[1], pair[0]))[
                :5
            ]
        ]
    items.sort(key=lambda item: (-int(item["size"]), int(item["cluster_id"])))
    return {"items": items[:limit], "truncated": max(0, len(items) - limit)}


def _symbol_meta(
    conn: sqlite3.Connection,
    root: Path,
    allowed_files: set[str] | None = None,
) -> dict[str, dict[str, Any]]:
    if not _table_exists(conn, "symbols") or not _table_exists(conn, "files"):
        return {}
    try:
        rows = conn.execute(
            """
            SELECT s.name, s.kind, s.start_line, s.end_line, f.path AS file
            FROM symbols s
            JOIN files f ON f.id = s.file_id
            WHERE f.path NOT LIKE ':%'
            ORDER BY s.name, f.path, s.start_line
            """
        ).fetchall()
    except sqlite3.Error:
        return {}
    meta: dict[str, dict[str, Any]] = {}
    for row in rows:
        if allowed_files is not None and str(row["file"]) not in allowed_files:
            continue
        if row["name"] in meta:
            continue
        meta[row["name"]] = {
            "symbol": row["name"],
            "uid": _compute_uid(row["file"], int(row["start_line"])),
            "kind": row["kind"],
            "file": _relativize(row["file"], root),
            "line": int(row["start_line"]),
            "end_line": int(row["end_line"]),
            "is_test": is_test_file(row["file"]),
        }
    return meta


def _edge_rows(
    conn: sqlite3.Connection,
    allowed_files: set[str] | None = None,
    *,
    include_self_edges: bool = True,
) -> list[dict[str, Any]]:
    if not _table_exists(conn, "edges"):
        return []
    edge_columns = _column_names(conn, "edges")
    synth_expr = "e.synthesized_by" if "synthesized_by" in edge_columns else "NULL"
    provenance_expr = "e.provenance" if "provenance" in edge_columns else "NULL"
    self_filter = "" if include_self_edges else "AND e.source_name != e.target_name"
    try:
        rows = conn.execute(
            f"""
            SELECT
                e.source_name,
                e.target_name,
                e.kind,
                e.line,
                e.confidence,
                {synth_expr} AS synthesized_by,
                {provenance_expr} AS provenance,
                f.path AS file
            FROM edges e
            JOIN files f ON f.id = e.file_id
            WHERE 1 = 1
            {self_filter}
            ORDER BY e.source_name, e.target_name, e.kind
            """
        ).fetchall()
    except sqlite3.Error:
        return []
    result: list[dict[str, Any]] = []
    for row in rows:
        if allowed_files is not None and str(row["file"]) not in allowed_files:
            continue
        result.append(
            {
                "source": row["source_name"],
                "target": row["target_name"],
                "kind": row["kind"],
                "line": row["line"],
                "confidence": row["confidence"],
                "synthesized_by": row["synthesized_by"],
                "provenance": row["provenance"],
                "file": row["file"],
            }
        )
    return result


def _topology_sections(
    conn: sqlite3.Connection,
    root: Path,
    allowed_files: set[str] | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    meta = _symbol_meta(conn, root, allowed_files)
    rows = _edge_rows(conn, allowed_files, include_self_edges=False)
    incoming: dict[str, int] = defaultdict(int)
    outgoing: dict[str, int] = defaultdict(int)
    outgoing_kinds: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    sources: set[str] = set()
    declared_targets: set[str] = set()
    for row in rows:
        source = str(row["source"])
        target = str(row["target"])
        kind = str(row["kind"])
        if source in meta:
            sources.add(source)
            outgoing[source] += 1
            outgoing_kinds[source][kind] += 1
        if target in meta:
            declared_targets.add(target)
            incoming[target] += 1

    def _item(name: str) -> dict[str, Any]:
        base = dict(
            meta.get(name)
            or {
                "symbol": name,
                "uid": None,
                "kind": None,
                "file": None,
                "line": None,
                "is_test": False,
            }
        )
        base["degrees"] = {
            "incoming": incoming.get(name, 0),
            "outgoing": outgoing.get(name, 0),
            "total": incoming.get(name, 0) + outgoing.get(name, 0),
        }
        return base

    entry_names = [
        name
        for name in sources - declared_targets
        if not bool(meta.get(name, {}).get("is_test", False))
    ]
    entry_points = sorted(
        (_item(name) for name in entry_names),
        key=lambda item: (-int(item["degrees"]["outgoing"]), str(item["symbol"])),
    )[:limit]

    hotspots = sorted(
        (_item(name) for name in incoming if incoming.get(name, 0) > 0),
        key=lambda item: (
            -int(item["degrees"]["incoming"]),
            bool(item["is_test"]),
            str(item["symbol"]),
        ),
    )[:limit]

    orchestrators: list[dict[str, Any]] = []
    for name in sources:
        if outgoing[name] <= 0:
            continue
        item = _item(name)
        item["edge_kinds"] = dict(sorted(outgoing_kinds[name].items()))
        orchestrators.append(item)
    orchestrators.sort(
        key=lambda item: (
            -int(item["degrees"]["outgoing"]),
            bool(item["is_test"]),
            str(item["symbol"]),
        )
    )

    return {
        "entry_points": {"items": entry_points, "truncated": max(0, len(entry_names) - limit)},
        "hotspots": {"items": hotspots, "truncated": max(0, len(incoming) - limit)},
        "orchestrators": {
            "items": orchestrators[:limit],
            "truncated": max(0, len(orchestrators) - limit),
        },
    }


def _edge_mix_section(rows: list[dict[str, Any]]) -> dict[str, Any]:
    edge_kinds: dict[str, int] = defaultdict(int)
    confidence: dict[str, int] = defaultdict(int)
    synthesized: dict[str, int] = defaultdict(int)
    for row in rows:
        if row["kind"] is not None:
            edge_kinds[str(row["kind"])] += 1
        if row["confidence"] is not None:
            confidence[str(row["confidence"])] += 1
        if row["synthesized_by"] is not None:
            synthesized[str(row["synthesized_by"])] += 1
    return {
        "edge_kinds": dict(sorted(edge_kinds.items())),
        "confidence": dict(sorted(confidence.items())),
        "synthesized": dict(sorted(synthesized.items())),
        "synthesized_total": sum(synthesized.values()),
    }


def _tests_section(
    edge_rows: list[dict[str, Any]],
    symbol_meta: dict[str, dict[str, Any]],
    *,
    production_files: int,
    test_files: int,
    limit: int,
) -> dict[str, Any]:
    test_edges = [row for row in edge_rows if row["kind"] == "tests"]
    incoming: dict[str, int] = defaultdict(int)
    outgoing: dict[str, int] = defaultdict(int)
    provenance: dict[str, int] = defaultdict(int)
    non_test_degree: dict[str, int] = defaultdict(int)
    for row in test_edges:
        incoming[str(row["target"])] += 1
        outgoing[str(row["source"])] += 1
        if row["synthesized_by"]:
            provenance[str(row["synthesized_by"])] += 1
    for row in edge_rows:
        if row["kind"] == "tests":
            continue
        non_test_degree[str(row["source"])] += 1
        non_test_degree[str(row["target"])] += 1

    def _target_item(name: str, count: int) -> dict[str, Any]:
        base = dict(
            symbol_meta.get(name)
            or {"symbol": name, "uid": None, "kind": None, "file": None, "line": None}
        )
        base["test_edges"] = count
        return base

    top_tested = [
        _target_item(name, count)
        for name, count in sorted(incoming.items(), key=lambda pair: (-pair[1], pair[0]))[:limit]
    ]
    test_heavy_sources = [
        {"source": name, "test_edges": count}
        for name, count in sorted(outgoing.items(), key=lambda pair: (-pair[1], pair[0]))[:limit]
    ]
    untested_hotspots = []
    for name, meta in sorted(
        symbol_meta.items(),
        key=lambda pair: (-non_test_degree.get(pair[0], 0), str(pair[1].get("file")), pair[0]),
    ):
        if bool(meta.get("is_test")) or name in incoming or non_test_degree.get(name, 0) <= 0:
            continue
        item = dict(meta)
        item["coupling_edges"] = non_test_degree[name]
        untested_hotspots.append(item)
        if len(untested_hotspots) >= limit:
            break
    truncated = max(0, len(incoming) - len(top_tested)) + max(
        0, len(outgoing) - len(test_heavy_sources)
    )
    return {
        "files": {"production": production_files, "test": test_files, "unknown": 0},
        "coverage_edges": {
            "status": "populated" if test_edges else "empty",
            "count": len(test_edges),
            "provenance": dict(sorted(provenance.items())),
        },
        "top_tested_symbols": top_tested,
        "test_heavy_sources": test_heavy_sources,
        "untested_hotspots": untested_hotspots,
        "truncated": truncated,
    }


def _boundary_section(
    conn: sqlite3.Connection,
    root: Path,
    allowed_files: set[str] | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    target_meta = _symbol_meta(conn, root, None)
    boundaries: dict[tuple[str, str], dict[str, Any]] = {}
    # Edges carry target names, not target symbol IDs. Homonyms therefore resolve
    # to the first indexed definition here; this section is an area-level signal,
    # not an exact per-definition dependency proof.
    for row in _edge_rows(conn, allowed_files, include_self_edges=False):
        source_area = _area_for_file(str(row["file"]), root)
        target = target_meta.get(str(row["target"]))
        target_file = target.get("file") if target else None
        if target_file is None:
            continue
        target_area = target_file.partition("/")[0] or "."
        if source_area == target_area:
            continue
        key = (source_area, target_area)
        bucket = boundaries.setdefault(
            key,
            {
                "source_area": source_area,
                "target_area": target_area,
                "edge_count": 0,
                "edge_kinds": {},
                "confidence": {},
                "representative": {
                    "source": row["source"],
                    "target": row["target"],
                    "edge_kind": row["kind"],
                },
            },
        )
        bucket["edge_count"] += 1
        bucket["edge_kinds"][row["kind"]] = bucket["edge_kinds"].get(row["kind"], 0) + 1
        bucket["confidence"][row["confidence"]] = bucket["confidence"].get(row["confidence"], 0) + 1
    items = sorted(
        boundaries.values(),
        key=lambda item: (
            -int(item["edge_count"]),
            str(item["source_area"]),
            str(item["target_area"]),
        ),
    )
    return {"items": items[:limit], "truncated": max(0, len(items) - limit)}


def _resolve_scope(
    conn: sqlite3.Connection,
    root: Path,
    scope: str | None,
) -> tuple[dict[str, Any], set[str] | None, Path | None, list[dict[str, str]]]:
    if not scope:
        return {"path": None, "applied": False}, None, None, []
    scope_path = Path(scope)
    abs_scope = (scope_path if scope_path.is_absolute() else root / scope_path).resolve()
    root_resolved = root.resolve()
    if abs_scope != root_resolved and not str(abs_scope).startswith(str(root_resolved) + "/"):
        return (
            {"path": scope, "applied": False},
            set(),
            None,
            [
                _warning(
                    "SCOPE_OUTSIDE_ROOT",
                    "Scope path is outside the project root.",
                    "Pass a root-relative path inside the indexed project.",
                )
            ],
        )
    prefix = str(abs_scope) + "/"
    allowed = {
        str(row["path"])
        for row in _file_rows(conn)
        if str(row["path"]) == str(abs_scope) or str(row["path"]).startswith(prefix)
    }
    warnings = []
    if not allowed:
        warnings.append(
            _warning(
                "SCOPE_EMPTY",
                "Scope did not match any indexed files.",
                "Check the path or run 'seam sync'.",
            )
        )
    return {"path": scope, "applied": True}, allowed, abs_scope, warnings


def _warning(code: str, message: str, hint: str) -> dict[str, str]:
    return {"code": code, "message": message, "hint": hint}


def _optional_surfaces() -> dict[str, Any]:
    return {
        name: {
            "status": "unsupported",
            "items": [],
            "reason": surface["message"],
        }
        for name, surface in _OPTIONAL_SURFACES.items()
        if name not in {"routes", "configs", "resources", "exceptions", "test_edges"}
    }


def _routes_section(
    conn: sqlite3.Connection,
    root: Path,
    allowed_files: set[str] | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    if not _table_exists(conn, "routes"):
        surface = _OPTIONAL_SURFACES["routes"]
        return {"status": "unsupported", "items": [], "reason": surface["message"], "truncated": 0}
    try:
        rows = conn.execute(
            """
            SELECT
                r.symbol_name,
                r.method,
                r.path,
                r.normalized_path,
                r.framework,
                r.handler,
                r.line,
                r.confidence,
                r.provenance,
                f.path AS file
            FROM routes r
            JOIN files f ON f.id = r.file_id
            WHERE f.path NOT LIKE ':%'
            ORDER BY r.method, r.normalized_path, f.path, r.line
            """
        ).fetchall()
    except sqlite3.Error:
        rows = []
    if allowed_files is not None:
        rows = [row for row in rows if str(row["file"]) in allowed_files]
    items = [
        {
            "symbol": row["symbol_name"],
            "uid": _compute_uid(str(row["file"]), int(row["line"])),
            "method": row["method"],
            "path": row["path"],
            "normalized_path": row["normalized_path"],
            "framework": row["framework"],
            "handler": row["handler"],
            "file": _relativize(str(row["file"]), root),
            "line": row["line"],
            "confidence": row["confidence"],
            "provenance": row["provenance"],
        }
        for row in rows[:limit]
    ]
    return {
        "status": "populated" if rows else "empty",
        "items": items,
        "truncated": max(0, len(rows) - limit),
    }


def _route_metadata_by_symbol(conn: sqlite3.Connection, root: Path) -> dict[str, dict[str, Any]]:
    if not _table_exists(conn, "routes"):
        return {}
    try:
        rows = conn.execute(
            """
            SELECT
                r.symbol_name,
                r.method,
                r.path,
                r.normalized_path,
                r.framework,
                r.handler,
                r.line,
                f.path AS file
            FROM routes r
            JOIN files f ON f.id = r.file_id
            WHERE f.path NOT LIKE ':%'
            ORDER BY r.symbol_name, f.path, r.line
            """
        ).fetchall()
    except sqlite3.Error:
        return {}
    metadata: dict[str, dict[str, Any]] = {}
    for row in rows:
        metadata.setdefault(
            str(row["symbol_name"]),
            {
                "method": row["method"],
                "path": row["path"],
                "normalized_path": row["normalized_path"],
                "framework": row["framework"],
                "handler": row["handler"],
                "file": _relativize(str(row["file"]), root),
                "line": int(row["line"]),
            },
        )
    return metadata


def _http_calls_section(
    conn: sqlite3.Connection,
    root: Path,
    edge_rows: list[dict[str, Any]],
    limit: int = 10,
) -> dict[str, Any]:
    if not _table_exists(conn, "routes") or not _table_exists(conn, "edges"):
        return {
            "status": "unsupported",
            "count": 0,
            "items": [],
            "reason": "HTTP-call evidence requires route metadata and graph edges.",
            "truncated": 0,
        }
    route_metadata = _route_metadata_by_symbol(conn, root)
    rows = [row for row in edge_rows if row["kind"] == "http_calls"]
    rows.sort(
        key=lambda row: (str(row["target"]), str(row["source"]), str(row["file"]), int(row["line"]))
    )
    items = [
        {
            "source": row["source"],
            "target": row["target"],
            "file": _relativize(str(row["file"]), root),
            "line": int(row["line"]),
            "confidence": row["confidence"],
            "provenance": row["provenance"],
            "route_resolved": str(row["target"]) in route_metadata,
            "route": route_metadata.get(str(row["target"])),
        }
        for row in rows[:limit]
    ]
    return {
        "status": "populated" if rows else "empty",
        "count": len(rows),
        "items": items,
        "truncated": max(0, len(rows) - limit),
    }


def _configs_section(
    conn: sqlite3.Connection,
    root: Path,
    allowed_files: set[str] | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    if not _table_exists(conn, "config_keys"):
        surface = _OPTIONAL_SURFACES["configs"]
        return {"status": "unsupported", "items": [], "reason": surface["message"], "truncated": 0}
    try:
        rows = conn.execute(
            """
            SELECT
                c.symbol_name,
                c.key,
                c.normalized_key,
                c.source_family,
                c.role,
                c.value_state,
                c.value_category,
                c.line,
                c.confidence,
                c.provenance,
                f.path AS file
            FROM config_keys c
            JOIN files f ON f.id = c.file_id
            WHERE f.path NOT LIKE ':%'
            ORDER BY c.normalized_key, c.role, f.path, c.line
            """
        ).fetchall()
    except sqlite3.Error:
        rows = []
    if allowed_files is not None:
        rows = [row for row in rows if str(row["file"]) in allowed_files]
    items = [
        {
            "symbol": row["symbol_name"],
            "uid": _compute_uid(str(row["file"]), int(row["line"])),
            "key": row["key"],
            "normalized_key": row["normalized_key"],
            "source_family": row["source_family"],
            "role": row["role"],
            "value_state": row["value_state"],
            "value_category": row["value_category"],
            "file": _relativize(str(row["file"]), root),
            "line": row["line"],
            "confidence": row["confidence"],
            "provenance": row["provenance"],
        }
        for row in rows[:limit]
    ]
    return {
        "status": "populated" if rows else "empty",
        "items": items,
        "truncated": max(0, len(rows) - limit),
    }


def _resources_section(
    conn: sqlite3.Connection,
    root: Path,
    allowed_files: set[str] | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    if not _table_exists(conn, "resources"):
        surface = _OPTIONAL_SURFACES["resources"]
        return {"status": "unsupported", "items": [], "reason": surface["message"], "truncated": 0}
    try:
        rows = conn.execute(
            """
            SELECT
                r.symbol_name,
                r.name,
                r.normalized_name,
                r.category,
                r.source_family,
                r.line,
                r.confidence,
                r.provenance,
                f.path AS file
            FROM resources r
            JOIN files f ON f.id = r.file_id
            WHERE f.path NOT LIKE ':%'
            ORDER BY r.category, r.normalized_name, f.path, r.line
            """
        ).fetchall()
    except sqlite3.Error:
        rows = []
    if allowed_files is not None:
        rows = [row for row in rows if str(row["file"]) in allowed_files]
    items = [
        {
            "symbol": row["symbol_name"],
            "uid": _compute_uid(str(row["file"]), int(row["line"])),
            "name": row["name"],
            "normalized_name": row["normalized_name"],
            "category": row["category"],
            "source_family": row["source_family"],
            "file": _relativize(str(row["file"]), root),
            "line": row["line"],
            "confidence": row["confidence"],
            "provenance": row["provenance"],
        }
        for row in rows[:limit]
    ]
    return {
        "status": "populated" if rows else "empty",
        "items": items,
        "truncated": max(0, len(rows) - limit),
    }


def _infra_section(
    conn: sqlite3.Connection,
    root: Path,
    allowed_files: set[str] | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    section = _resources_section(conn, root, allowed_files, limit=10_000)
    if section.get("status") == "unsupported":
        return section
    rows = [
        item for item in section["items"] if str(item.get("category")) in _INFRA_RESOURCE_CATEGORIES
    ]
    rows.sort(
        key=lambda item: (str(item["category"]), str(item["normalized_name"]), str(item["file"]))
    )
    return {
        "status": "populated" if rows else "empty",
        "items": rows[:limit],
        "truncated": max(0, len(rows) - limit),
    }


def _exceptions_section(
    conn: sqlite3.Connection,
    root: Path,
    allowed_files: set[str] | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    if not _table_exists(conn, "edges"):
        surface = _OPTIONAL_SURFACES["exceptions"]
        return {"status": "unsupported", "reason": surface["message"], "truncated": 0}
    try:
        rows = conn.execute(
            """
            SELECT
                e.source_name,
                e.target_name,
                e.kind,
                e.line,
                e.confidence,
                f.path AS file
            FROM edges e
            JOIN files f ON f.id = e.file_id
            WHERE e.kind IN ('raises', 'catches') AND f.path NOT LIKE ':%'
            ORDER BY e.kind, e.target_name, e.source_name, f.path, e.line
            """
        ).fetchall()
    except sqlite3.Error:
        rows = []
    if allowed_files is not None:
        rows = [row for row in rows if str(row["file"]) in allowed_files]

    raised: dict[str, int] = defaultdict(int)
    caught: dict[str, int] = defaultdict(int)
    source_counts: dict[str, dict[str, int]] = defaultdict(lambda: {"raises": 0, "catches": 0})
    broad_catches: list[dict[str, Any]] = []
    broad_names = {"BaseException", "Exception", "Error", "RuntimeError", "Throwable"}
    for row in rows:
        target = str(row["target_name"])
        kind = str(row["kind"])
        source = str(row["source_name"])
        if kind == "raises":
            raised[target] += 1
            source_counts[source]["raises"] += 1
        elif kind == "catches":
            caught[target] += 1
            source_counts[source]["catches"] += 1
            if target.rsplit(".", 1)[-1] in broad_names:
                broad_catches.append(
                    {
                        "source": source,
                        "target": target,
                        "file": _relativize(str(row["file"]), root),
                        "line": int(row["line"]),
                        "confidence": row["confidence"],
                    }
                )

    def _ranked_types(counts: dict[str, int]) -> list[dict[str, Any]]:
        return [
            {"target": target, "count": count}
            for target, count in sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))[
                :limit
            ]
        ]

    heavy_symbols = [
        {"source": source, **counts}
        for source, counts in sorted(
            source_counts.items(),
            key=lambda item: (-(item[1]["raises"] + item[1]["catches"]), item[0]),
        )[:limit]
    ]
    return {
        "status": "populated" if rows else "empty",
        "raised_types": _ranked_types(raised),
        "caught_types": _ranked_types(caught),
        "broad_catches": broad_catches[:limit],
        "heavy_symbols": heavy_symbols,
        "truncated": max(
            0,
            max(
                len(raised) - limit,
                len(caught) - limit,
                len(broad_catches) - limit,
                len(source_counts) - limit,
            ),
        ),
    }


def _warnings(
    *,
    freshness: dict[str, Any],
    counts: dict[str, int],
    routes_supported: bool,
    configs_supported: bool,
    resources_supported: bool,
    test_edges_supported: bool,
    exceptions_supported: bool,
) -> list[dict[str, str]]:
    warnings: list[dict[str, str]] = []
    if freshness["stale"]:
        warnings.append(
            _warning(
                "INDEX_STALE",
                freshness["reason"] or "The index may be stale.",
                freshness["hint"] or "Run 'seam sync' or 'seam init'.",
            )
        )
    if counts["files"] == 0 or counts["symbols"] == 0:
        warnings.append(
            _warning(
                "INDEX_EMPTY",
                "The index has no real files or symbols.",
                "Run 'seam init' from the project root.",
            )
        )
    for name, surface in _OPTIONAL_SURFACES.items():
        if name == "routes" and routes_supported:
            continue
        if name == "configs" and configs_supported:
            continue
        if name == "resources" and resources_supported:
            continue
        if name == "test_edges" and test_edges_supported:
            continue
        if name == "exceptions" and exceptions_supported:
            continue
        warnings.append(_warning(surface["code"], surface["message"], surface["hint"]))
    return warnings


def _next_calls() -> list[dict[str, Any]]:
    return [
        {
            "tool": "seam_graph_search",
            "reason": "Find concrete hotspots, dead-code suspects, field access, or inheritance relationships from the architecture overview.",
            "params": {"preset": "hotspot", "limit": 10},
        },
        {
            "tool": "seam_context",
            "reason": "Open a 360-degree view for any symbol selected from an architecture section before editing it.",
            "params": {"symbol": "<symbol>"},
        },
        {
            "tool": "seam_snippet",
            "reason": "Retrieve bounded source for any UID returned by architecture or graph-search results.",
            "params": {"uid": "<uid>"},
        },
        {
            "tool": "seam_impact",
            "reason": "Check production blast radius before changing a high fan-in symbol.",
            "params": {"target": "<symbol>", "direction": "upstream"},
        },
    ]


def _next_calls_for_sections(sections: dict[str, Any]) -> list[dict[str, Any]]:
    calls = _next_calls()
    hotspots = sections.get("hotspots", {}).get("items", [])
    if hotspots:
        top = hotspots[0]
        if top.get("uid"):
            calls.insert(
                1,
                {
                    "tool": "seam_context",
                    "reason": "Inspect the top fan-in hotspot before changing shared architecture.",
                    "params": {"symbol": top["symbol"], "uid": top["uid"]},
                },
            )
    orchestrators = sections.get("orchestrators", {}).get("items", [])
    if orchestrators:
        top = orchestrators[0]
        calls.insert(
            2,
            {
                "tool": "seam_impact",
                "reason": "Check the blast radius for the top fan-out orchestrator.",
                "params": {"target": top["symbol"], "direction": "downstream"},
            },
        )
    exceptions = sections.get("exceptions")
    if exceptions and exceptions.get("status") == "populated":
        calls.insert(
            1,
            {
                "tool": "seam_graph_search",
                "reason": "Review explicit failure paths using exception-flow edges.",
                "params": {"edge_kind": "raises,catches", "limit": 10},
            },
        )
        heavy_symbols = exceptions.get("heavy_symbols", [])
        if heavy_symbols:
            calls.insert(
                2,
                {
                    "tool": "seam_context",
                    "reason": "Inspect the symbol with the densest explicit exception-flow evidence.",
                    "params": {"symbol": heavy_symbols[0]["source"]},
                },
            )
    tests = sections.get("tests")
    coverage_edges = tests.get("coverage_edges", {}) if isinstance(tests, dict) else {}
    if coverage_edges.get("status") == "populated":
        calls.insert(
            1,
            {
                "tool": "seam_graph_search",
                "reason": "Review static test-to-production evidence.",
                "params": {"edge_kind": "tests", "limit": 10},
            },
        )
    http_calls = sections.get("http_calls")
    if isinstance(http_calls, dict) and http_calls.get("status") == "populated":
        calls.insert(
            1,
            {
                "tool": "seam_graph_search",
                "reason": "Review static HTTP caller-to-route evidence.",
                "params": {"edge_kind": "http_calls", "direction": "outgoing", "limit": 10},
            },
        )
    return calls


def _normalise_sections(sections: list[str] | None) -> list[str]:
    if not sections:
        return list(_DEFAULT_SECTIONS)
    seen: set[str] = set()
    result: list[str] = []
    for section in sections:
        name = section.strip()
        if not name:
            continue
        if name not in _SECTION_ORDER:
            raise ValueError(f"unknown architecture section: {name}")
        if name not in seen:
            result.append(name)
            seen.add(name)
    return result or list(_DEFAULT_SECTIONS)


def _trim_list_section(section: dict[str, Any]) -> int:
    items = section.get("items")
    if not isinstance(items, list) or not items:
        return 0
    items.pop()
    section["truncated"] = int(section.get("truncated") or 0) + 1
    return 1


def _fit_to_byte_budget(result: dict[str, Any], *, max_bytes: int) -> dict[str, Any]:
    if max_bytes <= 0:
        return result
    omitted = 0
    priority = (
        "clusters",
        "physical",
        "boundaries",
        "orchestrators",
        "entry_points",
        "hotspots",
        "languages",
        "routes",
        "http_calls",
        "configs",
        "resources",
        "infra",
        "exceptions",
    )

    def _size() -> int:
        return len(json.dumps(result, separators=(",", ":"), sort_keys=True).encode("utf-8"))

    def _trim_one() -> int:
        # Trim highest-volume evidence first and preserve summary/count identity
        # as long as possible; agents can recover omitted rows with narrower
        # sections, higher limits, or follow-up graph/search calls.
        trimmed = 0
        for section_name in priority:
            section = result.get("sections", {}).get(section_name)
            if isinstance(section, dict):
                if (
                    section_name == "physical"
                    and isinstance(section.get("top_areas"), list)
                    and section["top_areas"]
                ):
                    section["top_areas"].pop()
                    section["truncated"] = int(section.get("truncated") or 0) + 1
                    trimmed = 1
                    break
                trimmed = _trim_list_section(section)
                if trimmed:
                    break
        if trimmed:
            return trimmed
        physical = result.get("sections", {}).get("physical")
        if (
            isinstance(physical, dict)
            and isinstance(physical.get("structure"), dict)
            and physical["structure"]
            and physical["structure"].get("truncated") is not True
        ):
            physical["structure"] = {"truncated": True, "reason": "omitted to fit max_bytes"}
            return 1
        sections = result.get("sections")
        if isinstance(sections, dict):
            for section_name in priority + ("optional_surfaces", "edge_mix", "tests", "summary"):
                if section_name in sections:
                    del sections[section_name]
                    return 1
        if not trimmed:
            next_calls = result.get("next_calls")
            if isinstance(next_calls, list) and next_calls:
                next_calls.pop()
                return 1
            warnings = result.get("warnings")
            if isinstance(warnings, list) and warnings:
                warnings.pop()
                return 1
        return 0

    while _size() > max_bytes:
        trimmed = _trim_one()
        if not trimmed:
            break
        omitted += trimmed
    if omitted:
        result.setdefault("truncation", {})["byte_budget"] = {
            "limit": max_bytes,
            "omitted": omitted,
            "unit": "compact_json_bytes",
        }
        result.setdefault("warnings", []).append(
            _warning(
                "BYTE_BUDGET_EXCEEDED",
                "Architecture output was trimmed to fit the requested byte budget.",
                "Increase max_bytes or request fewer sections if you need the omitted rows.",
            )
        )
        while _size() > max_bytes:
            trimmed = _trim_one()
            if not trimmed:
                break
            result["truncation"]["byte_budget"]["omitted"] += trimmed
    return result


def describe_architecture(
    conn: sqlite3.Connection,
    *,
    root: Path,
    scope: str | None = None,
    sections: list[str] | None = None,
    limit: int = 10,
    max_bytes: int = 0,
) -> dict[str, Any]:
    """Return a compact read-only architecture briefing for the indexed project."""
    meta = _metadata(conn)
    safe_limit = max(1, min(limit, 100))
    scope_info, allowed_files, scope_path, scope_warnings = _resolve_scope(conn, root, scope)
    staleness = check_staleness(conn, root=root, respect_knob=False)
    freshness = {
        "stale": bool(staleness["stale"]),
        "reason": staleness["reason"] or None,
        "hint": staleness["hint"] or None,
    }

    files = _file_rows(conn, allowed_files)
    test_files, production_files = _test_file_counts(files)
    edge_rows = _edge_rows(conn, allowed_files, include_self_edges=True)
    counts = {
        "files": len(files),
        "symbols": _count_joined(conn, "symbols", allowed_files),
        "edges": len(edge_rows),
        "clusters": _count(conn, "clusters"),
        "comments": _count(conn, "comments"),
        "import_mappings": _count(conn, "import_mappings"),
        "embeddings": _count(conn, "embeddings"),
        "routes": _count(conn, "routes"),
        "http_calls": sum(1 for row in edge_rows if row["kind"] == "http_calls"),
        "config_keys": _count(conn, "config_keys"),
        "resources": _count(conn, "resources"),
        "test_files": test_files,
        "production_files": production_files,
        "unknown_files": 0,
    }

    requested_sections = _normalise_sections(sections)
    all_sections: dict[str, Any] = {
        "summary": {
            "text": (
                f"{counts['files']} indexed files, {counts['symbols']} symbols, "
                f"{counts['edges']} edges, {counts['clusters']} clusters."
            )
        },
        "languages": _language_section(conn, allowed_files),
        "physical": _physical_section(conn, root, allowed_files, scope_path, safe_limit),
        "clusters": _cluster_section(conn, root, allowed_files, safe_limit),
        "routes": _routes_section(conn, root, allowed_files, safe_limit),
        "http_calls": _http_calls_section(conn, root, edge_rows, safe_limit),
        "configs": _configs_section(conn, root, allowed_files, safe_limit),
        "resources": _resources_section(conn, root, allowed_files, safe_limit),
        "infra": _infra_section(conn, root, allowed_files, safe_limit),
        "exceptions": _exceptions_section(conn, root, allowed_files, safe_limit),
        "edge_mix": _edge_mix_section(edge_rows),
        "tests": _tests_section(
            edge_rows,
            _symbol_meta(conn, root, allowed_files),
            production_files=production_files,
            test_files=test_files,
            limit=safe_limit,
        ),
        "optional_surfaces": _optional_surfaces(),
    }
    all_sections.update(_topology_sections(conn, root, allowed_files, safe_limit))
    all_sections["boundaries"] = _boundary_section(conn, root, allowed_files, safe_limit)
    selected_sections = {
        name: all_sections[name]
        for name in sorted(requested_sections, key=lambda section: _SECTION_ORDER[section])
        if name in all_sections
    }

    result: dict[str, Any] = {
        "identity": {
            "schema_version": _schema_version(meta),
            "seam_version": seam.__version__,
            "index_seam_version": meta.get("seam_version"),
        },
        "freshness": freshness,
        "scope": scope_info,
        "counts": counts,
        "sections": selected_sections,
        "warnings": [],
        "truncation": {},
        "next_calls": _next_calls_for_sections(selected_sections),
    }
    result["warnings"] = [
        *scope_warnings,
        *_warnings(
            freshness=freshness,
            counts=counts,
            routes_supported=_table_exists(conn, "routes"),
            configs_supported=_table_exists(conn, "config_keys"),
            resources_supported=_table_exists(conn, "resources"),
            test_edges_supported=any(row["kind"] == "tests" for row in edge_rows),
            exceptions_supported=_table_exists(conn, "edges"),
        ),
    ]
    return _fit_to_byte_budget(result, max_bytes=max_bytes)
