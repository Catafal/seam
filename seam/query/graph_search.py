"""Typed structural graph search over the existing Seam index.

Owns: validating graph-search filters, computing edge-aware degree metrics,
pagination, sorting, and optional one-hop previews.
Does not own: text/semantic search, source retrieval, graph mutation, or Cypher.

The implementation intentionally works from the current SQLite graph schema
instead of exposing raw SQL. Agents get recurring graph questions answered
through typed parameters while Seam keeps the query path local, read-only, and
bounded.
"""

from __future__ import annotations

import fnmatch
import hashlib
import re
import sqlite3
from collections import defaultdict
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, Literal, TypedDict, cast

from seam.query.graph_recipes import compile_graph_search_recipe

DEFAULT_LIMIT = 20
MAX_LIMIT = 100
DEFAULT_PREVIEW_LIMIT = 3
MAX_PREVIEW_LIMIT = 10
MAX_REGEX_PATTERN_LEN = 200

VALID_DIRECTIONS = {"incoming", "outgoing", "both"}
VALID_SORTS = {
    "default",
    "in-degree",
    "out-degree",
    "total-degree",
    "name",
    "file",
    "line",
}
VALID_CONFIDENCE = {"EXTRACTED", "INFERRED", "AMBIGUOUS"}
VALID_SYNTHESIZED = {"any", "parser", "synthesized"}
VALID_TEST_SCOPE = {"any", "test", "source"}
VALID_PRESETS = {"dead-code", "hotspot", "field-access", "inheritance", "isolates"}
DEFAULT_SYMBOL_KINDS = {
    "function",
    "class",
    "method",
    "interface",
    "type",
    "field",
    "route",
    "config",
    "resource",
}
DEFAULT_EDGE_KINDS = {
    "call",
    "import",
    "extends",
    "implements",
    "instantiates",
    "holds",
    "reads",
    "writes",
    "uses",
    "http_calls",
    "reads_config",
    "configures",
    "raises",
    "catches",
    "tests",
}


class GraphSearchResult(TypedDict):
    query: dict[str, Any]
    items: list[dict[str, Any]]
    total: int
    limit: int
    offset: int
    has_more: bool
    warnings: list[dict[str, str]]


def _warning(code: str, message: str, hint: str) -> dict[str, str]:
    return {"code": code, "message": message, "hint": hint}


def _invalid_input(message: str) -> dict[str, str]:
    return {"error": "INVALID_INPUT", "message": message}


def _invalid_query(message: str) -> dict[str, str]:
    return {"error": "INVALID_QUERY", "message": message}


def _compute_uid(file_path: str, start_line: int) -> str:
    digest = hashlib.sha1(file_path.encode("utf-8")).hexdigest()[:8]
    return f"{digest}:{start_line}"


def _relativize(path: str, root: Path) -> str:
    try:
        return str(Path(path).resolve(strict=False).relative_to(root))
    except ValueError:
        return f"<outside-root>/{Path(path).name}"


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


def _normalise_pattern(pattern: str | None, *, regex: bool) -> Callable[[str | None], bool]:
    if not pattern:
        return lambda _value: True
    if regex:
        compiled = re.compile(pattern)
        return lambda value: bool(value is not None and compiled.search(value))
    return lambda value: bool(value is not None and fnmatch.fnmatchcase(value, pattern))


def _is_test_path(path: str) -> bool:
    normalized = path.replace("\\", "/").lower()
    return (
        "/test/" in normalized
        or "/tests/" in normalized
        or normalized.startswith("test/")
        or normalized.startswith("tests/")
        or normalized.endswith("_test.py")
        or normalized.endswith(".test.ts")
        or normalized.endswith(".spec.ts")
        or normalized.endswith(".test.js")
        or normalized.endswith(".spec.js")
    )


def _apply_preset(
    preset: str | None,
    *,
    edge_kind: str | None,
    direction: str,
    max_degree: int | None,
    max_in_degree: int | None,
    min_in_degree: int | None,
    min_out_degree: int | None,
    sort: str,
) -> tuple[str | None, str, int | None, int | None, int | None, int | None, str]:
    if preset is None:
        return edge_kind, direction, max_degree, max_in_degree, min_in_degree, min_out_degree, sort
    if preset == "dead-code":
        return edge_kind or "call", "incoming", max_degree, 0 if max_in_degree is None else max_in_degree, min_in_degree, min_out_degree, sort
    if preset == "hotspot":
        return edge_kind, "incoming", max_degree, max_in_degree, 2 if min_in_degree is None else min_in_degree, min_out_degree, "in-degree" if sort == "default" else sort
    if preset == "field-access":
        return edge_kind or "reads,writes", "both", max_degree, max_in_degree, min_in_degree, min_out_degree, sort
    if preset == "inheritance":
        return edge_kind or "extends,implements", "both", max_degree, max_in_degree, min_in_degree, min_out_degree, sort
    if preset == "isolates":
        return edge_kind, "both", 0 if max_degree is None else max_degree, max_in_degree, min_in_degree, min_out_degree, sort
    return edge_kind, direction, max_degree, max_in_degree, min_in_degree, min_out_degree, sort


def _split_csv(value: str | None) -> set[str] | None:
    if value is None:
        return None
    parts = {part.strip() for part in value.split(",") if part.strip()}
    return parts or None


def _fetch_symbols(conn: sqlite3.Connection, root: Path) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    warnings: list[dict[str, str]] = []
    symbol_columns = _column_names(conn, "symbols")
    file_columns = _column_names(conn, "files")
    clusters_available = _table_exists(conn, "clusters") and "cluster_id" in symbol_columns

    optional_exprs = {
        "signature": "s.signature" if "signature" in symbol_columns else "NULL",
        "qualified_name": "s.qualified_name" if "qualified_name" in symbol_columns else "NULL",
        "visibility": "s.visibility" if "visibility" in symbol_columns else "NULL",
        "is_exported": "s.is_exported" if "is_exported" in symbol_columns else "NULL",
        "cluster_id": "s.cluster_id" if "cluster_id" in symbol_columns else "NULL",
        "language": "f.language" if "language" in file_columns else "NULL",
    }
    cluster_select = "c.label" if clusters_available else "NULL"
    cluster_join = "LEFT JOIN clusters c ON c.id = s.cluster_id" if clusters_available else ""
    if "signature" not in symbol_columns or "qualified_name" not in symbol_columns:
        warnings.append(_warning(
            "MISSING_SYMBOL_ENRICHMENT",
            "Some symbol enrichment columns are unavailable.",
            "Run 'seam init' with the current Seam version to rebuild enrichment data.",
        ))

    rows = conn.execute(
        f"""
        SELECT
            s.name,
            s.kind,
            s.start_line,
            s.end_line,
            {optional_exprs["signature"]} AS signature,
            {optional_exprs["qualified_name"]} AS qualified_name,
            {optional_exprs["visibility"]} AS visibility,
            {optional_exprs["is_exported"]} AS is_exported,
            {optional_exprs["cluster_id"]} AS cluster_id,
            {cluster_select} AS cluster_label,
            f.path AS file,
            {optional_exprs["language"]} AS language
        FROM symbols s
        JOIN files f ON f.id = s.file_id
        {cluster_join}
        WHERE f.path NOT LIKE ':%'
        ORDER BY s.name, f.path, s.start_line
        """
    ).fetchall()
    return [
        {
            "symbol": row["name"],
            "kind": row["kind"],
            "line": row["start_line"],
            "end_line": row["end_line"],
            "signature": row["signature"],
            "qualified_name": row["qualified_name"],
            "visibility": row["visibility"],
            "is_exported": None if row["is_exported"] is None else bool(row["is_exported"]),
            "cluster_id": row["cluster_id"],
            "cluster_label": row["cluster_label"],
            "file_abs": row["file"],
            "file": _relativize(row["file"], root),
            "language": row["language"],
            "uid": _compute_uid(row["file"], row["start_line"]),
        }
        for row in rows
    ], warnings


def _fetch_edges(conn: sqlite3.Connection) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    edge_columns = _column_names(conn, "edges")
    warnings: list[dict[str, str]] = []
    synthesized_expr = "e.synthesized_by" if "synthesized_by" in edge_columns else "NULL"
    receiver_expr = "e.receiver" if "receiver" in edge_columns else "NULL"
    provenance_expr = "e.provenance" if "provenance" in edge_columns else "NULL"
    if "synthesized_by" not in edge_columns:
        warnings.append(_warning(
            "MISSING_SYNTHESIZED_PROVENANCE",
            "The index does not expose synthesized edge provenance.",
            "Run 'seam init' with the current Seam version if provenance filters are needed.",
        ))
    rows = conn.execute(
        f"""
        SELECT
            e.source_name,
            e.target_name,
            e.kind,
            e.line,
            e.confidence,
            {receiver_expr} AS receiver,
            {synthesized_expr} AS synthesized_by,
            {provenance_expr} AS provenance,
            f.path AS file
        FROM edges e
        JOIN files f ON f.id = e.file_id
        """
    ).fetchall()
    return [
        {
            "source": row["source_name"],
            "target": row["target_name"],
            "kind": row["kind"],
            "line": row["line"],
            "confidence": row["confidence"],
            "receiver": row["receiver"],
            "synthesized_by": row["synthesized_by"],
            "provenance": row["provenance"],
            "file": row["file"],
        }
        for row in rows
    ], warnings


def _edge_matches(
    edge: dict[str, Any],
    *,
    edge_kinds: set[str] | None,
    confidence: str | None,
    synthesized: str,
) -> bool:
    if edge_kinds is not None and edge["kind"] not in edge_kinds:
        return False
    if confidence is not None and edge["confidence"] != confidence:
        return False
    if synthesized == "parser" and edge["synthesized_by"] is not None:
        return False
    if synthesized == "synthesized" and edge["synthesized_by"] is None:
        return False
    return True


def _degree_maps(
    symbols: Iterable[dict[str, Any]],
    edges: Iterable[dict[str, Any]],
    *,
    edge_kinds: set[str] | None,
    confidence: str | None,
    synthesized: str,
) -> tuple[dict[str, int], dict[str, int], dict[str, list[dict[str, Any]]], dict[str, list[dict[str, Any]]]]:
    names = {symbol["symbol"] for symbol in symbols}
    incoming: dict[str, int] = defaultdict(int)
    outgoing: dict[str, int] = defaultdict(int)
    incoming_edges: dict[str, list[dict[str, Any]]] = defaultdict(list)
    outgoing_edges: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for edge in edges:
        if not _edge_matches(
            edge,
            edge_kinds=edge_kinds,
            confidence=confidence,
            synthesized=synthesized,
        ):
            continue
        if edge["target"] in names:
            incoming[edge["target"]] += 1
            incoming_edges[edge["target"]].append(edge)
        if edge["source"] in names:
            outgoing[edge["source"]] += 1
            outgoing_edges[edge["source"]].append(edge)
    return incoming, outgoing, incoming_edges, outgoing_edges


def _passes_degree_filters(
    *,
    incoming: int,
    outgoing: int,
    direction: str,
    min_degree: int | None,
    max_degree: int | None,
    min_in_degree: int | None,
    max_in_degree: int | None,
    min_out_degree: int | None,
    max_out_degree: int | None,
    edge_filter_active: bool,
) -> bool:
    total = incoming + outgoing
    match_degree = (
        incoming if direction == "incoming" else outgoing if direction == "outgoing" else total
    )
    checks = [
        (min_degree is None or total >= min_degree),
        (max_degree is None or total <= max_degree),
        (min_in_degree is None or incoming >= min_in_degree),
        (max_in_degree is None or incoming <= max_in_degree),
        (min_out_degree is None or outgoing >= min_out_degree),
        (max_out_degree is None or outgoing <= max_out_degree),
    ]
    if not all(checks):
        return False
    has_degree_filter = any(
        value is not None
        for value in (
            min_degree,
            max_degree,
            min_in_degree,
            max_in_degree,
            min_out_degree,
            max_out_degree,
        )
    )
    if edge_filter_active and not has_degree_filter and match_degree == 0:
        return False
    return True


def _sort_items(items: list[dict[str, Any]], sort: str) -> list[dict[str, Any]]:
    if sort == "in-degree":
        return sorted(items, key=lambda item: (-item["degrees"]["incoming"], item["symbol"], item["file"], item["line"]))
    if sort == "out-degree":
        return sorted(items, key=lambda item: (-item["degrees"]["outgoing"], item["symbol"], item["file"], item["line"]))
    if sort == "total-degree":
        return sorted(items, key=lambda item: (-item["degrees"]["total"], item["symbol"], item["file"], item["line"]))
    if sort == "name":
        return sorted(items, key=lambda item: (item["symbol"], item["file"], item["line"]))
    if sort == "file":
        return sorted(items, key=lambda item: (item["file"], item["line"], item["symbol"]))
    if sort == "line":
        return sorted(items, key=lambda item: (item["line"], item["symbol"], item["file"]))
    return sorted(items, key=lambda item: (item["symbol"], item["file"], item["line"]))


def _preview(
    symbol: str,
    *,
    root: Path,
    symbol_by_name: dict[str, list[dict[str, Any]]],
    incoming_edges: dict[str, list[dict[str, Any]]],
    outgoing_edges: dict[str, list[dict[str, Any]]],
    direction: str,
    limit: int,
) -> tuple[list[dict[str, Any]], bool]:
    edges: list[tuple[str, dict[str, Any]]] = []
    if direction in {"incoming", "both"}:
        edges.extend(("incoming", edge) for edge in incoming_edges.get(symbol, []))
    if direction in {"outgoing", "both"}:
        edges.extend(("outgoing", edge) for edge in outgoing_edges.get(symbol, []))
    edges = sorted(edges, key=lambda pair: (pair[0], pair[1]["kind"], pair[1]["source"], pair[1]["target"]))
    truncated = len(edges) > limit
    items: list[dict[str, Any]] = []
    for edge_direction, edge in edges[:limit]:
        other_name = edge["source"] if edge_direction == "incoming" else edge["target"]
        candidates = symbol_by_name.get(other_name, [])
        other = candidates[0] if candidates else None
        route_resolved = None
        if edge["kind"] == "http_calls":
            route_candidates = symbol_by_name.get(str(edge["target"]), [])
            route_resolved = any(candidate["kind"] == "route" for candidate in route_candidates)
        items.append({
            "direction": edge_direction,
            "symbol": other_name,
            "uid": other["uid"] if other else None,
            "kind": other["kind"] if other else None,
            "file": other["file"] if other else _relativize(edge["file"], root),
            "line": other["line"] if other else edge["line"],
            "edge_kind": edge["kind"],
            "confidence": edge["confidence"],
            "receiver": edge["receiver"],
            "synthesized_by": edge["synthesized_by"],
            "provenance": edge["provenance"],
            "route_resolved": route_resolved,
        })
    return items, truncated


def _validate_non_negative(name: str, value: int | None) -> dict[str, str] | None:
    if value is not None and value < 0:
        return _invalid_input(f"{name} must be non-negative")
    return None


def _validate_graph_search_inputs(
    *,
    kind: str | None,
    direction: str,
    confidence: str | None,
    synthesized: str,
    test_scope: str,
    preset: str | None,
    sort: str,
    name_pattern: str | None,
    qualified_name_pattern: str | None,
    file_pattern: str | None,
    regex: bool,
    min_degree: int | None,
    max_degree: int | None,
    min_in_degree: int | None,
    max_in_degree: int | None,
    min_out_degree: int | None,
    max_out_degree: int | None,
    limit: int,
    offset: int,
    preview_limit: int,
) -> dict[str, str] | None:
    if kind is not None and kind not in DEFAULT_SYMBOL_KINDS:
        return _invalid_input(f"unknown symbol kind: {kind}")
    if direction not in VALID_DIRECTIONS:
        return _invalid_input(f"unknown direction: {direction}")
    if confidence is not None and confidence not in VALID_CONFIDENCE:
        return _invalid_input(f"unknown confidence: {confidence}")
    if synthesized not in VALID_SYNTHESIZED:
        return _invalid_input(f"unknown synthesized filter: {synthesized}")
    if test_scope not in VALID_TEST_SCOPE:
        return _invalid_input(f"unknown test_scope: {test_scope}")
    if preset is not None and preset not in VALID_PRESETS:
        return _invalid_input(f"unknown preset: {preset}")
    if sort not in VALID_SORTS:
        return _invalid_input(f"unknown sort: {sort}")
    if regex:
        for pattern in (name_pattern, qualified_name_pattern, file_pattern):
            if pattern is not None and len(pattern) > MAX_REGEX_PATTERN_LEN:
                return _invalid_input(
                    f"regex patterns must be {MAX_REGEX_PATTERN_LEN} characters or fewer"
                )
    for name, value in (
        ("min_degree", min_degree),
        ("max_degree", max_degree),
        ("min_in_degree", min_in_degree),
        ("max_in_degree", max_in_degree),
        ("min_out_degree", min_out_degree),
        ("max_out_degree", max_out_degree),
        ("limit", limit),
        ("offset", offset),
        ("preview_limit", preview_limit),
    ):
        invalid = _validate_non_negative(name, value)
        if invalid:
            return invalid
    return None


def _matches_symbol_filters(
    symbol_row: dict[str, Any],
    *,
    kind: str | None,
    language: str | None,
    cluster_id: int | None,
    visibility: str | None,
    is_exported: bool | None,
    test_scope: str,
    name_matches: Callable[[str | None], bool],
    qualified_matches: Callable[[str | None], bool],
    file_matches: Callable[[str | None], bool],
) -> bool:
    if kind is not None and symbol_row["kind"] != kind:
        return False
    if language is not None and symbol_row["language"] != language:
        return False
    if cluster_id is not None and symbol_row["cluster_id"] != cluster_id:
        return False
    if visibility is not None and symbol_row["visibility"] != visibility:
        return False
    if is_exported is not None and symbol_row["is_exported"] is not is_exported:
        return False
    if test_scope == "test" and not _is_test_path(symbol_row["file"]):
        return False
    if test_scope == "source" and _is_test_path(symbol_row["file"]):
        return False
    return (
        name_matches(symbol_row["symbol"])
        and qualified_matches(symbol_row["qualified_name"])
        and file_matches(symbol_row["file"])
    )


def _build_symbol_item(
    symbol_row: dict[str, Any],
    *,
    in_degree: int,
    out_degree: int,
) -> dict[str, Any]:
    return {
        "symbol": symbol_row["symbol"],
        "uid": symbol_row["uid"],
        "kind": symbol_row["kind"],
        "file": symbol_row["file"],
        "line": symbol_row["line"],
        "end_line": symbol_row["end_line"],
        "signature": symbol_row["signature"],
        "qualified_name": symbol_row["qualified_name"],
        "visibility": symbol_row["visibility"],
        "is_exported": symbol_row["is_exported"],
        "language": symbol_row["language"],
        "cluster_id": symbol_row["cluster_id"],
        "cluster_label": symbol_row["cluster_label"],
        "is_test": _is_test_path(symbol_row["file"]),
        "degrees": {
            "incoming": in_degree,
            "outgoing": out_degree,
            "total": in_degree + out_degree,
        },
    }


def _collect_symbol_items(
    *,
    symbols: list[dict[str, Any]],
    root: Path,
    symbol_by_name: dict[str, list[dict[str, Any]]],
    incoming: dict[str, int],
    outgoing: dict[str, int],
    incoming_edges: dict[str, list[dict[str, Any]]],
    outgoing_edges: dict[str, list[dict[str, Any]]],
    kind: str | None,
    language: str | None,
    cluster_id: int | None,
    visibility: str | None,
    is_exported: bool | None,
    test_scope: str,
    name_matches: Callable[[str | None], bool],
    qualified_matches: Callable[[str | None], bool],
    file_matches: Callable[[str | None], bool],
    normalized_direction: Literal["incoming", "outgoing", "both"],
    min_degree: int | None,
    max_degree: int | None,
    min_in_degree: int | None,
    max_in_degree: int | None,
    min_out_degree: int | None,
    max_out_degree: int | None,
    edge_filter_active: bool,
    include_preview: bool,
    safe_preview_limit: int,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for symbol_row in symbols:
        in_degree = incoming.get(symbol_row["symbol"], 0)
        out_degree = outgoing.get(symbol_row["symbol"], 0)
        if not _matches_symbol_filters(
            symbol_row,
            kind=kind,
            language=language,
            cluster_id=cluster_id,
            visibility=visibility,
            is_exported=is_exported,
            test_scope=test_scope,
            name_matches=name_matches,
            qualified_matches=qualified_matches,
            file_matches=file_matches,
        ):
            continue
        if not _passes_degree_filters(
            incoming=in_degree,
            outgoing=out_degree,
            direction=normalized_direction,
            min_degree=min_degree,
            max_degree=max_degree,
            min_in_degree=min_in_degree,
            max_in_degree=max_in_degree,
            min_out_degree=min_out_degree,
            max_out_degree=max_out_degree,
            edge_filter_active=edge_filter_active,
        ):
            continue
        item = _build_symbol_item(
            symbol_row,
            in_degree=in_degree,
            out_degree=out_degree,
        )
        if include_preview:
            preview, truncated = _preview(
                symbol_row["symbol"],
                root=root,
                symbol_by_name=symbol_by_name,
                incoming_edges=incoming_edges,
                outgoing_edges=outgoing_edges,
                direction=normalized_direction,
                limit=safe_preview_limit,
            )
            item["preview"] = preview
            if truncated:
                item["preview_truncated"] = True
        items.append(item)
    return items


def graph_search(
    conn: sqlite3.Connection,
    *,
    root: Path,
    kind: str | None = None,
    name_pattern: str | None = None,
    qualified_name_pattern: str | None = None,
    file_pattern: str | None = None,
    language: str | None = None,
    edge_kind: str | None = None,
    direction: Literal["incoming", "outgoing", "both"] = "both",
    min_degree: int | None = None,
    max_degree: int | None = None,
    min_in_degree: int | None = None,
    max_in_degree: int | None = None,
    min_out_degree: int | None = None,
    max_out_degree: int | None = None,
    confidence: str | None = None,
    synthesized: Literal["any", "parser", "synthesized"] = "any",
    cluster_id: int | None = None,
    visibility: str | None = None,
    is_exported: bool | None = None,
    test_scope: Literal["any", "test", "source"] = "any",
    preset: str | None = None,
    sort: str = "default",
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
    include_preview: bool = False,
    preview_limit: int = DEFAULT_PREVIEW_LIMIT,
    regex: bool = False,
    recipe: str | None = None,
) -> GraphSearchResult | dict[str, str]:
    """Return a bounded structural search page using typed filters only."""
    recipe_metadata: dict[str, Any] | None = None
    if recipe is not None:
        recipe_result = compile_graph_search_recipe(
            recipe,
            {
                "kind": kind,
                "name_pattern": name_pattern,
                "qualified_name_pattern": qualified_name_pattern,
                "file_pattern": file_pattern,
                "language": language,
                "edge_kind": edge_kind,
                "direction": direction,
                "min_degree": min_degree,
                "max_degree": max_degree,
                "min_in_degree": min_in_degree,
                "max_in_degree": max_in_degree,
                "min_out_degree": min_out_degree,
                "max_out_degree": max_out_degree,
                "confidence": confidence,
                "synthesized": synthesized,
                "cluster_id": cluster_id,
                "visibility": visibility,
                "is_exported": is_exported,
                "test_scope": test_scope,
                "preset": preset,
                "sort": sort,
                "limit": limit,
                "offset": offset,
                "include_preview": include_preview,
                "preview_limit": preview_limit,
                "regex": regex,
            },
        )
        if isinstance(recipe_result, dict):
            return recipe_result
        compiled, recipe_metadata = recipe_result
        kind = compiled["kind"]
        name_pattern = compiled["name_pattern"]
        qualified_name_pattern = compiled["qualified_name_pattern"]
        file_pattern = compiled["file_pattern"]
        language = compiled["language"]
        edge_kind = compiled["edge_kind"]
        direction = cast(Literal["incoming", "outgoing", "both"], compiled["direction"])
        min_degree = compiled["min_degree"]
        max_degree = compiled["max_degree"]
        min_in_degree = compiled["min_in_degree"]
        max_in_degree = compiled["max_in_degree"]
        min_out_degree = compiled["min_out_degree"]
        max_out_degree = compiled["max_out_degree"]
        confidence = compiled["confidence"]
        synthesized = cast(Literal["any", "parser", "synthesized"], compiled["synthesized"])
        cluster_id = compiled["cluster_id"]
        visibility = compiled["visibility"]
        is_exported = compiled["is_exported"]
        test_scope = cast(Literal["any", "test", "source"], compiled["test_scope"])
        preset = compiled["preset"]
        sort = compiled["sort"]
        limit = compiled["limit"]
        offset = compiled["offset"]
        include_preview = compiled["include_preview"]
        preview_limit = compiled["preview_limit"]
        regex = compiled["regex"]

    invalid = _validate_graph_search_inputs(
        kind=kind,
        direction=direction,
        confidence=confidence,
        synthesized=synthesized,
        test_scope=test_scope,
        preset=preset,
        sort=sort,
        name_pattern=name_pattern,
        qualified_name_pattern=qualified_name_pattern,
        file_pattern=file_pattern,
        regex=regex,
        min_degree=min_degree,
        max_degree=max_degree,
        min_in_degree=min_in_degree,
        max_in_degree=max_in_degree,
        min_out_degree=min_out_degree,
        max_out_degree=max_out_degree,
        limit=limit,
        offset=offset,
        preview_limit=preview_limit,
    )
    if invalid:
        return invalid
    safe_limit = min(limit, MAX_LIMIT)
    safe_preview_limit = min(preview_limit, MAX_PREVIEW_LIMIT)

    (
        edge_kind,
        preset_direction,
        max_degree,
        max_in_degree,
        min_in_degree,
        min_out_degree,
        sort,
    ) = _apply_preset(
        preset,
        edge_kind=edge_kind,
        direction=direction,
        max_degree=max_degree,
        max_in_degree=max_in_degree,
        min_in_degree=min_in_degree,
        min_out_degree=min_out_degree,
        sort=sort,
    )
    normalized_direction = cast(Literal["incoming", "outgoing", "both"], preset_direction)
    edge_kinds = _split_csv(edge_kind)
    if edge_kinds is not None:
        unknown = edge_kinds - DEFAULT_EDGE_KINDS
        if unknown:
            return _invalid_input(f"unknown edge kind: {sorted(unknown)[0]}")

    try:
        name_matches = _normalise_pattern(name_pattern, regex=regex)
        qualified_matches = _normalise_pattern(qualified_name_pattern, regex=regex)
        file_matches = _normalise_pattern(file_pattern, regex=regex)
    except re.error as exc:
        return _invalid_query(f"invalid regex pattern: {exc}")

    root = root.resolve()
    symbols, symbol_warnings = _fetch_symbols(conn, root)
    edges, edge_warnings = _fetch_edges(conn)
    warnings = [*symbol_warnings, *edge_warnings]
    incoming, outgoing, incoming_edges, outgoing_edges = _degree_maps(
        symbols,
        edges,
        edge_kinds=edge_kinds,
        confidence=confidence,
        synthesized=synthesized,
    )
    symbol_by_name: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for symbol_row in symbols:
        symbol_by_name[symbol_row["symbol"]].append(symbol_row)

    edge_filter_active = edge_kinds is not None or confidence is not None or synthesized != "any"
    items = _collect_symbol_items(
        symbols=symbols,
        root=root,
        symbol_by_name=symbol_by_name,
        incoming=incoming,
        outgoing=outgoing,
        incoming_edges=incoming_edges,
        outgoing_edges=outgoing_edges,
        kind=kind,
        language=language,
        cluster_id=cluster_id,
        visibility=visibility,
        is_exported=is_exported,
        test_scope=test_scope,
        name_matches=name_matches,
        qualified_matches=qualified_matches,
        file_matches=file_matches,
        normalized_direction=normalized_direction,
        min_degree=min_degree,
        max_degree=max_degree,
        min_in_degree=min_in_degree,
        max_in_degree=max_in_degree,
        min_out_degree=min_out_degree,
        max_out_degree=max_out_degree,
        edge_filter_active=edge_filter_active,
        include_preview=include_preview,
        safe_preview_limit=safe_preview_limit,
    )

    sorted_items = _sort_items(items, sort)
    total = len(sorted_items)
    page = sorted_items[offset: offset + safe_limit]
    if safe_limit < limit:
        warnings.append(_warning(
            "LIMIT_CLAMPED",
            f"limit was clamped to {MAX_LIMIT}.",
            "Use pagination with offset for additional pages.",
        ))
    if include_preview and any(item.get("preview_truncated") for item in page):
        warnings.append(_warning(
            "PREVIEW_TRUNCATED",
            "One or more connected previews were capped.",
            "Increase preview_limit within the documented maximum or inspect the symbol directly.",
        ))
    query = {
        "kind": kind,
        "name_pattern": name_pattern,
        "qualified_name_pattern": qualified_name_pattern,
        "file_pattern": file_pattern,
        "language": language,
        "edge_kind": edge_kind,
        "direction": normalized_direction,
        "min_degree": min_degree,
        "max_degree": max_degree,
        "min_in_degree": min_in_degree,
        "max_in_degree": max_in_degree,
        "min_out_degree": min_out_degree,
        "max_out_degree": max_out_degree,
        "confidence": confidence,
        "synthesized": synthesized,
        "cluster_id": cluster_id,
        "visibility": visibility,
        "is_exported": is_exported,
        "test_scope": test_scope,
        "preset": preset,
        "sort": sort,
        "limit": safe_limit,
        "offset": offset,
        "include_preview": include_preview,
        "preview_limit": safe_preview_limit,
        "regex": regex,
        "recipe": recipe,
    }
    result = {
        "query": query,
        "items": page,
        "total": total,
        "limit": safe_limit,
        "offset": offset,
        "has_more": offset + safe_limit < total,
        "warnings": warnings,
    }
    if recipe_metadata is not None:
        result["recipe"] = recipe_metadata
    return cast(GraphSearchResult, result)
