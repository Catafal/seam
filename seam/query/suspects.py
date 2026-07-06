"""Conservative cleanup suspect analysis over the existing Seam index.

Owns: combining static graph evidence into cleanup-review candidates.
Does not own: proving code is dead, deleting code, reading source bodies, or
adding new graph facts.
"""

from __future__ import annotations

import fnmatch
import hashlib
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any, Literal, TypedDict, cast

import seam.config as config

VALID_MODES = {"symbols", "files"}
VALID_TEST_SCOPES = {"source", "test", "any"}
PRODUCTION_EDGE_KINDS = {
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
}
SPECIAL_FILE_MARKERS = (
    "__init__.py",
    "conftest.py",
    "manage.py",
    "settings.py",
)
SPECIAL_PATH_PARTS = {
    "migrations",
    "fixtures",
    "scripts",
    "generated",
    "vendor",
    "node_modules",
}


class EvidenceRef(TypedDict, total=False):
    source: str
    symbol: str | None
    file: str | None
    line: int | None
    edge_kind: str | None
    confidence: str | None
    provenance: str | None
    direction: str | None
    note: str


class SuspectCandidate(TypedDict, total=False):
    kind: str
    symbol: str
    file: str
    line: int | None
    language: str | None
    symbol_kind: str | None
    uid: str | None
    suspect_strength: str
    removal_risk: str
    reasons: list[str]
    blockers: list[str]
    evidence: list[EvidenceRef]
    caveats: list[str]
    recommended_next_calls: list[dict[str, Any]]
    omitted: dict[str, int]


class SuspectResult(TypedDict):
    mode: str
    found: bool
    query: dict[str, Any]
    candidates: list[SuspectCandidate]
    summary: dict[str, Any]
    caveats: list[str]
    warnings: list[dict[str, str]]
    recommended_next_calls: list[dict[str, Any]]
    omitted: dict[str, int]


def _invalid_input(message: str) -> dict[str, str]:
    return {"error": "INVALID_INPUT", "message": message}


def _warning(code: str, message: str, hint: str) -> dict[str, str]:
    return {"code": code, "message": message, "hint": hint}


def _relativize(path: str | None, root: Path) -> str | None:
    if path is None:
        return None
    try:
        return str(Path(path).resolve(strict=False).relative_to(root))
    except ValueError:
        return f"<outside-root>/{Path(path).name}"


def _uid(file_path: str | None, line: int | None) -> str | None:
    if file_path is None or line is None:
        return None
    digest = hashlib.sha1(file_path.encode("utf-8")).hexdigest()[:8]
    return f"{digest}:{line}"


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


def _is_test_path(path: str | None) -> bool:
    if not path:
        return False
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


def _is_special_file(rel_path: str) -> bool:
    normalized = rel_path.replace("\\", "/").lower()
    parts = set(normalized.split("/"))
    return normalized.endswith(SPECIAL_FILE_MARKERS) or bool(parts & SPECIAL_PATH_PARTS)


def _matches_scope(path: str | None, test_scope: str) -> bool:
    if test_scope == "any":
        return True
    is_test = _is_test_path(path)
    return is_test if test_scope == "test" else not is_test


def _matches_target_symbol(symbol: dict[str, Any], target: str | None) -> bool:
    if target is None:
        return True
    return target in {
        symbol["symbol"],
        symbol.get("qualified_name"),
        symbol.get("uid"),
        symbol.get("file"),
    }


def _matches_target_file(
    file_row: dict[str, Any],
    target: str | None,
    symbols_by_file: dict[str, list[dict[str, Any]]],
) -> bool:
    if target is None or file_row["file"] == target:
        return True
    return any(_matches_target_symbol(symbol, target) for symbol in symbols_by_file[file_row["file"]])


def _fetch_symbols(conn: sqlite3.Connection, root: Path) -> list[dict[str, Any]]:
    symbol_columns = _column_names(conn, "symbols")
    file_columns = _column_names(conn, "files")
    optional_exprs = {
        "signature": "s.signature" if "signature" in symbol_columns else "NULL",
        "qualified_name": "s.qualified_name" if "qualified_name" in symbol_columns else "NULL",
        "visibility": "s.visibility" if "visibility" in symbol_columns else "NULL",
        "is_exported": "s.is_exported" if "is_exported" in symbol_columns else "NULL",
        "language": "f.language" if "language" in file_columns else "NULL",
    }
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
            f.path AS file,
            {optional_exprs["language"]} AS language
        FROM symbols s
        JOIN files f ON f.id = s.file_id
        WHERE f.path NOT LIKE ':%'
        ORDER BY f.path, s.start_line, s.name
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
            "file_abs": row["file"],
            "file": _relativize(row["file"], root),
            "language": row["language"],
            "uid": _uid(row["file"], row["start_line"]),
        }
        for row in rows
    ]


def _fetch_files(conn: sqlite3.Connection, root: Path) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT path, language FROM files WHERE path NOT LIKE ':%' ORDER BY path"
    ).fetchall()
    return [
        {
            "file_abs": row["path"],
            "file": _relativize(row["path"], root),
            "language": row["language"],
        }
        for row in rows
    ]


def _fetch_edges(conn: sqlite3.Connection, root: Path) -> list[dict[str, Any]]:
    edge_columns = _column_names(conn, "edges")
    synthesized_expr = "e.synthesized_by" if "synthesized_by" in edge_columns else "NULL"
    provenance_expr = "e.provenance" if "provenance" in edge_columns else "NULL"
    rows = conn.execute(
        f"""
        SELECT
            e.source_name,
            e.target_name,
            e.kind,
            e.line,
            e.confidence,
            {synthesized_expr} AS synthesized_by,
            {provenance_expr} AS provenance,
            f.path AS file
        FROM edges e
        JOIN files f ON f.id = e.file_id
        ORDER BY e.source_name, e.target_name, e.kind, e.line
        """
    ).fetchall()
    return [
        {
            "source": row["source_name"],
            "target": row["target_name"],
            "kind": row["kind"],
            "line": row["line"],
            "confidence": row["confidence"],
            "synthesized_by": row["synthesized_by"],
            "provenance": row["provenance"],
            "file_abs": row["file"],
            "file": _relativize(row["file"], root),
        }
        for row in rows
    ]


def _evidence(edge: dict[str, Any], *, direction: str) -> EvidenceRef:
    other = edge["source"] if direction == "incoming" else edge["target"]
    return {
        "source": "edges",
        "symbol": other,
        "file": edge["file"],
        "line": edge["line"],
        "edge_kind": edge["kind"],
        "confidence": edge["confidence"],
        "provenance": edge["synthesized_by"] or edge["provenance"],
        "direction": direction,
    }


def _cap_list(values: list[Any], limit: int) -> tuple[list[Any], int]:
    if limit <= 0 or len(values) <= limit:
        return values, 0
    return values[:limit], len(values) - limit


def _risk(blockers: list[str]) -> str:
    high = {
        "incoming_production_edge",
        "public_api_surface",
        "route_entrypoint",
        "operational_config_resource",
        "inheritance_or_implementation_evidence",
    }
    if high & set(blockers):
        return "high"
    if blockers:
        return "medium"
    return "unknown"


def _strength(reasons: list[str], blockers: list[str]) -> str:
    if "no_indexed_symbols" in blockers:
        return "weak"
    if not reasons:
        return "weak"
    if blockers:
        return "weak" if len(blockers) >= 2 else "moderate"
    return "strong" if len(reasons) >= 2 else "moderate"


def _symbol_next_calls(symbol: str, uid: str | None) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = [
        {
            "tool": "seam_context",
            "reason": "Verify callers, callees, field access, and test evidence before cleanup.",
            "params": {"symbol": symbol},
        },
        {
            "tool": "seam_impact",
            "reason": "Run blast-radius analysis before deleting or changing this suspect.",
            "params": {"target": symbol, "include_tests": True},
        },
    ]
    if uid:
        calls.insert(
            0,
            {
                "tool": "seam_snippet",
                "reason": "Read bounded source for the exact suspect before editing.",
                "params": {"uid": uid},
            },
        )
    return calls


def _file_next_calls(path: str) -> list[dict[str, Any]]:
    return [
        {
            "tool": "seam_graph_search",
            "reason": "Inspect indexed symbols inside this file before cleanup.",
            "params": {"file_pattern": path, "limit": 20},
        },
        {
            "tool": "seam_plan",
            "reason": "After deletion edits, use diff planning to inspect and test the actual change.",
            "params": {"mode": "diff", "scope": "working"},
        },
    ]


def _build_symbol_candidate(
    symbol: dict[str, Any],
    *,
    incoming: list[dict[str, Any]],
    outgoing: list[dict[str, Any]],
) -> SuspectCandidate:
    file_path = cast(str, symbol["file"])
    inbound_prod = [
        edge
        for edge in incoming
        if edge["kind"] in PRODUCTION_EDGE_KINDS and not _is_test_path(edge["file"])
    ]
    inbound_imports = [edge for edge in incoming if edge["kind"] == "import"]
    test_edges = [edge for edge in incoming if edge["kind"] == "tests" or _is_test_path(edge["file"])]
    field_edges = [edge for edge in incoming + outgoing if edge["kind"] in {"reads", "writes"}]
    inheritance_edges = [
        edge for edge in incoming + outgoing if edge["kind"] in {"extends", "implements", "instantiates"}
    ]
    operational_edges = [
        edge for edge in incoming + outgoing if edge["kind"] in {"reads_config", "configures"}
    ]
    route_edges = [edge for edge in incoming + outgoing if edge["kind"] == "http_calls"]
    reasons: list[str] = []
    blockers: list[str] = []
    caveats: list[str] = []
    evidence: list[EvidenceRef] = []

    if not inbound_prod:
        reasons.append("no_incoming_production_edges")
    else:
        blockers.append("incoming_production_edge")
        evidence.extend(_evidence(edge, direction="incoming") for edge in inbound_prod)
    if not inbound_imports:
        reasons.append("no_incoming_imports")
    if not test_edges:
        reasons.append("no_static_test_evidence")
    else:
        blockers.append("static_test_evidence")
        evidence.extend(_evidence(edge, direction="incoming") for edge in test_edges)
    if symbol["visibility"] in {"private", "internal"} or symbol["is_exported"] is False:
        reasons.append("private_or_internal")
    if symbol["visibility"] == "public" or symbol["is_exported"] is True:
        blockers.append("public_api_surface")
    if not incoming and not outgoing:
        reasons.append("isolated_static_graph_node")
    if symbol["kind"] == "route" or str(symbol["symbol"]).startswith("ROUTE "):
        blockers.append("route_entrypoint")
    if route_edges:
        blockers.append("route_entrypoint")
        evidence.extend(_evidence(edge, direction="incoming") for edge in route_edges)
    if symbol["kind"] in {"config", "resource"} or operational_edges:
        blockers.append("operational_config_resource")
        evidence.extend(_evidence(edge, direction="incoming") for edge in operational_edges)
    if symbol["kind"] == "field" and field_edges:
        blockers.append("field_access_evidence")
        evidence.extend(_evidence(edge, direction="incoming") for edge in field_edges)
    if symbol["kind"] in {"class", "interface", "type"} and inheritance_edges:
        blockers.append("inheritance_or_implementation_evidence")
        evidence.extend(_evidence(edge, direction="incoming") for edge in inheritance_edges)
    if _is_special_file(file_path):
        blockers.append("special_file_convention")
        caveats.append("Special entrypoint or generated-style path needs manual verification.")
    if any(edge["confidence"] in {"INFERRED", "AMBIGUOUS"} for edge in incoming + outgoing):
        caveats.append("Some relationships are inferred or ambiguous; verify with snippets.")

    evidence_limit = config.SEAM_SUSPECTS_MAX_EVIDENCE
    signal_limit = config.SEAM_SUSPECTS_MAX_SIGNALS
    capped_reasons, omitted_reasons = _cap_list(reasons, signal_limit)
    capped_blockers, omitted_blockers = _cap_list(sorted(dict.fromkeys(blockers)), signal_limit)
    capped_evidence, omitted_evidence = _cap_list(evidence, evidence_limit)
    omitted = {
        key: value
        for key, value in {
            "reasons": omitted_reasons,
            "blockers": omitted_blockers,
            "evidence": omitted_evidence,
        }.items()
        if value
    }
    return {
        "kind": "symbol",
        "symbol": symbol["symbol"],
        "file": file_path,
        "line": symbol["line"],
        "language": symbol["language"],
        "symbol_kind": symbol["kind"],
        "uid": symbol["uid"],
        "suspect_strength": _strength(reasons, blockers),
        "removal_risk": _risk(blockers),
        "reasons": cast(list[str], capped_reasons),
        "blockers": cast(list[str], capped_blockers),
        "evidence": cast(list[EvidenceRef], capped_evidence),
        "caveats": caveats,
        "recommended_next_calls": _symbol_next_calls(symbol["symbol"], symbol["uid"]),
        "omitted": omitted,
    }


def _build_file_candidate(
    file_row: dict[str, Any],
    *,
    symbols: list[dict[str, Any]],
    incoming_imports: list[dict[str, Any]],
    incoming_symbol_edges: list[dict[str, Any]],
    ambiguous_symbol_edges: list[dict[str, Any]],
) -> SuspectCandidate:
    rel_path = cast(str, file_row["file"])
    reasons: list[str] = []
    blockers: list[str] = []
    caveats: list[str] = []
    evidence: list[EvidenceRef] = []
    if not incoming_imports:
        reasons.append("file_has_no_incoming_imports")
    else:
        blockers.append("incoming_import_evidence")
        evidence.extend(_evidence(edge, direction="incoming") for edge in incoming_imports)
    if not incoming_symbol_edges:
        reasons.append("contained_symbols_have_no_incoming_production_edges")
    else:
        blockers.append("contained_symbol_usage")
        evidence.extend(_evidence(edge, direction="incoming") for edge in incoming_symbol_edges)
    if ambiguous_symbol_edges:
        caveats.append(
            "Some incoming edges target names that exist in multiple files; treat file usage as ambiguous."
        )
        evidence.extend(
            {
                **_evidence(edge, direction="incoming"),
                "note": "ambiguous target name; not used as a blocker",
            }
            for edge in ambiguous_symbol_edges
        )
    if not symbols:
        blockers.append("no_indexed_symbols")
        caveats.append("No indexed symbols were found in this file; this is coverage uncertainty.")
    if _is_test_path(rel_path):
        blockers.append("test_file")
    if _is_special_file(rel_path):
        blockers.append("special_file_convention")
        caveats.append("Special entrypoint or generated-style path needs manual verification.")

    evidence_limit = config.SEAM_SUSPECTS_MAX_EVIDENCE
    capped_evidence, omitted_evidence = _cap_list(evidence, evidence_limit)
    return {
        "kind": "file",
        "file": rel_path,
        "line": None,
        "language": file_row["language"],
        "symbol_kind": None,
        "uid": None,
        "suspect_strength": _strength(reasons, blockers),
        "removal_risk": _risk(blockers),
        "reasons": reasons[: config.SEAM_SUSPECTS_MAX_SIGNALS],
        "blockers": sorted(dict.fromkeys(blockers))[: config.SEAM_SUSPECTS_MAX_SIGNALS],
        "evidence": cast(list[EvidenceRef], capped_evidence),
        "caveats": caveats,
        "recommended_next_calls": _file_next_calls(rel_path),
        "omitted": {"evidence": omitted_evidence} if omitted_evidence else {},
    }


def _candidate_rank(candidate: SuspectCandidate) -> tuple[int, int, str, int]:
    strength_rank = {"strong": 0, "moderate": 1, "weak": 2}
    risk_rank = {"unknown": 0, "medium": 1, "high": 2}
    return (
        strength_rank.get(candidate["suspect_strength"], 9),
        risk_rank.get(candidate["removal_risk"], 9),
        candidate.get("file", ""),
        candidate.get("line") or 0,
    )


def _mode_caveats(warnings: list[dict[str, str]]) -> list[str]:
    caveats = [
        "Static graph evidence is not deletion proof.",
        "Absence of indexed evidence does not prove absence of runtime usage.",
        "Dynamic imports, reflection, framework registration, external APIs, generated code, and config-driven usage may be invisible.",
    ]
    if warnings:
        caveats.append("Some optional index capabilities are missing; empty evidence sections may be unsupported.")
    return caveats


def _capability_warnings(conn: sqlite3.Connection) -> list[dict[str, str]]:
    symbol_kinds = _group_counts(conn, "symbols", "kind")
    edge_kinds = _group_counts(conn, "edges", "kind")
    warnings: list[dict[str, str]] = []
    if edge_kinds.get("tests", 0) == 0:
        warnings.append(_warning(
            "NO_TEST_EDGES",
            "No static test edges are indexed.",
            "Treat missing test blockers as unknown, not as no tests.",
        ))
    if symbol_kinds.get("field", 0) == 0:
        warnings.append(_warning(
            "NO_FIELD_SYMBOLS",
            "No field symbols are indexed.",
            "Unused-field suspicion may be incomplete.",
        ))
    if symbol_kinds.get("route", 0) == 0:
        warnings.append(_warning(
            "NO_ROUTE_NODES",
            "No route nodes are indexed.",
            "Route entrypoint blockers may be incomplete.",
        ))
    return warnings


def _group_counts(conn: sqlite3.Connection, table: str, column: str) -> dict[str, int]:
    if column not in _column_names(conn, table):
        return {}
    rows = conn.execute(
        f"SELECT {column} AS key, COUNT(*) AS count FROM {table} GROUP BY {column}"
    ).fetchall()
    return {str(row["key"]): int(row["count"]) for row in rows if row["key"] is not None}


def _summary(candidates: list[SuspectCandidate], returned: int) -> dict[str, Any]:
    by_strength: dict[str, int] = {}
    by_risk: dict[str, int] = {}
    for candidate in candidates:
        by_strength[candidate["suspect_strength"]] = by_strength.get(
            candidate["suspect_strength"], 0
        ) + 1
        by_risk[candidate["removal_risk"]] = by_risk.get(candidate["removal_risk"], 0) + 1
    return {
        "total": len(candidates),
        "returned": returned,
        "omitted": max(len(candidates) - returned, 0),
        "by_suspect_strength": by_strength,
        "by_removal_risk": by_risk,
    }


def _found_false(mode: str, query: dict[str, Any], target: str) -> SuspectResult:
    return {
        "mode": mode,
        "found": False,
        "query": query,
        "candidates": [],
        "summary": _summary([], 0),
        "caveats": [f"Target {target!r} was not found in the index."],
        "warnings": [],
        "recommended_next_calls": [
            {
                "tool": "seam_search",
                "reason": "Search for the target before cleanup review.",
                "params": {"text": target},
            }
        ],
        "omitted": {},
    }


def analyze_suspects(
    conn: sqlite3.Connection,
    root: Path,
    *,
    mode: Literal["symbols", "files"] = "symbols",
    target: str | None = None,
    file_pattern: str | None = None,
    kind: str | None = None,
    language: str | None = None,
    visibility: str | None = None,
    is_exported: bool | None = None,
    test_scope: Literal["source", "test", "any"] = "source",
    limit: int | None = None,
) -> SuspectResult | dict[str, str]:
    """Return bounded cleanup-review suspects without claiming deletion safety."""
    if mode not in VALID_MODES:
        return _invalid_input(f"mode must be one of {sorted(VALID_MODES)}; got {mode!r}")
    if test_scope not in VALID_TEST_SCOPES:
        return _invalid_input(
            f"test_scope must be one of {sorted(VALID_TEST_SCOPES)}; got {test_scope!r}"
        )
    if limit is not None and limit < 0:
        return _invalid_input("limit must be non-negative")

    root = root.resolve()
    safe_limit = config.SEAM_SUSPECTS_MAX_CANDIDATES if limit is None else min(
        limit,
        config.SEAM_SUSPECTS_MAX_CANDIDATES,
    )
    symbols = _fetch_symbols(conn, root)
    files = _fetch_files(conn, root)
    edges = _fetch_edges(conn, root)
    warnings = _capability_warnings(conn)
    query: dict[str, Any] = {
        "mode": mode,
        "target": target,
        "file_pattern": file_pattern,
        "kind": kind,
        "language": language,
        "visibility": visibility,
        "is_exported": is_exported,
        "test_scope": test_scope,
        "limit": safe_limit,
    }

    incoming_by_name: dict[str, list[dict[str, Any]]] = defaultdict(list)
    outgoing_by_name: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for edge in edges:
        incoming_by_name[edge["target"]].append(edge)
        outgoing_by_name[edge["source"]].append(edge)

    if mode == "symbols":
        selected = [
            symbol
            for symbol in symbols
            if _matches_target_symbol(symbol, target)
            and (file_pattern is None or fnmatch.fnmatchcase(symbol["file"] or "", file_pattern))
            and (kind is None or symbol["kind"] == kind)
            and (language is None or symbol["language"] == language)
            and (visibility is None or symbol["visibility"] == visibility)
            and (is_exported is None or symbol["is_exported"] is is_exported)
            and _matches_scope(symbol["file"], test_scope)
        ]
        if target is not None and not selected:
            return _found_false(mode, query, target)
        candidates = [
            _build_symbol_candidate(
                symbol,
                incoming=incoming_by_name.get(symbol["symbol"], []),
                outgoing=outgoing_by_name.get(symbol["symbol"], []),
            )
            for symbol in selected
        ]
        candidates = [
            candidate
            for candidate in candidates
            if target is not None
            or "no_incoming_production_edges" in candidate["reasons"]
            or "isolated_static_graph_node" in candidate["reasons"]
        ]
    else:
        symbols_by_file: dict[str, list[dict[str, Any]]] = defaultdict(list)
        symbol_files_by_name: dict[str, set[str]] = defaultdict(set)
        for symbol in symbols:
            symbols_by_file[symbol["file"]].append(symbol)
            symbol_files_by_name[symbol["symbol"]].add(symbol["file"])
        selected_files = [
            file_row
            for file_row in files
            if _matches_target_file(file_row, target, symbols_by_file)
            and (file_pattern is None or fnmatch.fnmatchcase(file_row["file"] or "", file_pattern))
            and (language is None or file_row["language"] == language)
            and _matches_scope(file_row["file"], test_scope)
        ]
        if target is not None and not selected_files:
            return _found_false(mode, query, target)
        candidates = []
        for file_row in selected_files:
            contained = symbols_by_file.get(file_row["file"], [])
            contained_names = {symbol["symbol"] for symbol in contained}
            incoming_imports = [
                edge
                for edge in edges
                if edge["kind"] == "import" and symbol_files_by_name[edge["target"]] == {file_row["file"]}
            ]
            ambiguous_edges = [
                edge
                for edge in edges
                if edge["target"] in contained_names
                and file_row["file"] in symbol_files_by_name[edge["target"]]
                and len(symbol_files_by_name[edge["target"]]) > 1
                and edge["kind"] in PRODUCTION_EDGE_KINDS
                and not _is_test_path(edge["file"])
            ]
            incoming_symbol_edges = [
                edge
                for edge in edges
                if edge["target"] in contained_names
                and symbol_files_by_name[edge["target"]] == {file_row["file"]}
                and edge["kind"] in PRODUCTION_EDGE_KINDS
                and not _is_test_path(edge["file"])
            ]
            candidates.append(
                _build_file_candidate(
                    file_row,
                    symbols=contained,
                    incoming_imports=incoming_imports,
                    incoming_symbol_edges=incoming_symbol_edges,
                    ambiguous_symbol_edges=ambiguous_edges,
                )
            )

    ranked = sorted(candidates, key=_candidate_rank)
    page = ranked[:safe_limit]
    omitted_candidates = max(len(ranked) - len(page), 0)
    return {
        "mode": mode,
        "found": True,
        "query": query,
        "candidates": page,
        "summary": _summary(ranked, len(page)),
        "caveats": _mode_caveats(warnings),
        "warnings": warnings,
        "recommended_next_calls": [
            {
                "tool": "seam_graph_search",
                "reason": "Use raw graph-search recipes to inspect the structural basis for suspects.",
                "params": {"recipe": "dead-code-suspects", "limit": safe_limit},
            },
            {
                "tool": "seam_plan",
                "reason": "After cleanup edits, plan the actual diff before handoff.",
                "params": {"mode": "diff", "scope": "working"},
            },
        ],
        "omitted": {"candidates": omitted_candidates} if omitted_candidates else {},
    }
