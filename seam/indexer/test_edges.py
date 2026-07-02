"""Whole-index materialization for static test-to-production evidence.

Owns: deriving `tests` edges from existing graph evidence after all files are indexed.
Does not own: runtime coverage, assertion quality, fixture data flow, or test execution.
"""

from __future__ import annotations

import logging
import sqlite3
from collections import defaultdict
from dataclasses import dataclass

from seam.analysis.testpaths import is_test_file

logger = logging.getLogger(__name__)

_EXACT_SOURCE_KINDS = {
    "call": "test-call",
    "instantiates": "test-instantiates",
}
# Prefer direct execution evidence over naming heuristics when several static
# signals connect the same test and target.
_PROVENANCE_RANK = {
    "test-call": 4,
    "test-instantiates": 4,
    "test-import": 3,
    "test-name-proximity": 1,
}


@dataclass(frozen=True)
class _Candidate:
    source: str
    target: str
    line: int
    file_id: int
    confidence: str
    provenance: str


def _is_test_symbol(name: str) -> bool:
    final = name.rsplit(".", 1)[-1]
    owner = name.split(".", 1)[0]
    return final.startswith("test") or owner.startswith("Test")


@dataclass(frozen=True)
class _SymbolRow:
    name: str
    file_id: int
    path: str
    line: int


def _load_symbols(
    conn: sqlite3.Connection,
) -> tuple[dict[str, set[int]], dict[str, set[int]], dict[int, list[_SymbolRow]]]:
    test_symbols: dict[str, set[int]] = defaultdict(set)
    production_symbols: dict[str, set[int]] = defaultdict(set)
    symbols_by_file: dict[int, list[_SymbolRow]] = defaultdict(list)
    rows = conn.execute(
        """
        SELECT s.name, s.qualified_name, s.file_id, s.start_line, f.path
        FROM symbols s
        JOIN files f ON f.id = s.file_id
        WHERE f.path NOT LIKE ':%'
        ORDER BY f.path, s.start_line, s.name
        """
    ).fetchall()
    for row in rows:
        file_id = int(row["file_id"])
        symbol = _SymbolRow(
            name=str(row["name"]),
            file_id=file_id,
            path=str(row["path"]),
            line=int(row["start_line"]),
        )
        symbols_by_file[file_id].append(symbol)
        names = {row["name"]}
        if row["qualified_name"]:
            names.add(row["qualified_name"])
        target = test_symbols if is_test_file(row["path"]) else production_symbols
        for name in names:
            target[str(name)].add(file_id)
    return test_symbols, production_symbols, symbols_by_file


def _confidence_for_target(target: str, production_symbols: dict[str, set[int]]) -> str | None:
    count = len(production_symbols.get(target, set()))
    if count == 1:
        return "EXTRACTED"
    if count > 1:
        return "AMBIGUOUS"
    return None


def _collect_exact_candidates(conn: sqlite3.Connection) -> list[_Candidate]:
    test_symbols, production_symbols, _symbols_by_file = _load_symbols(conn)
    rows = conn.execute(
        """
        SELECT e.source_name, e.target_name, e.kind, e.line, e.file_id
        FROM edges e
        JOIN files f ON f.id = e.file_id
        WHERE e.kind IN ('call', 'instantiates')
          AND e.synthesized_by IS NULL
          AND f.path NOT LIKE ':%'
        ORDER BY e.source_name, e.target_name, e.kind, e.line
        """
    ).fetchall()

    candidates: list[_Candidate] = []
    for row in rows:
        source = str(row["source_name"])
        target = str(row["target_name"])
        file_id = int(row["file_id"])
        if file_id not in test_symbols.get(source, set()):
            continue
        if not _is_test_symbol(source):
            continue
        if file_id in test_symbols.get(target, set()):
            continue
        confidence = _confidence_for_target(target, production_symbols)
        if confidence is None:
            continue
        candidates.append(_Candidate(
            source=source,
            target=target,
            line=int(row["line"]),
            file_id=file_id,
            confidence=confidence,
            provenance=_EXACT_SOURCE_KINDS[str(row["kind"])],
        ))
    return candidates


def _test_symbols_in_file(symbols_by_file: dict[int, list[_SymbolRow]], file_id: int) -> list[_SymbolRow]:
    return [
        symbol
        for symbol in symbols_by_file.get(file_id, [])
        if _is_test_symbol(symbol.name)
    ]


def _test_name_mentions_target(test_name: str, target_name: str) -> bool:
    target_final = target_name.rsplit(".", 1)[-1].lower()
    test_final = test_name.rsplit(".", 1)[-1].lower()
    return target_final in test_final.removeprefix("test")


def _collect_import_candidates(conn: sqlite3.Connection) -> list[_Candidate]:
    _test_symbols, production_symbols, symbols_by_file = _load_symbols(conn)
    rows = conn.execute(
        """
        SELECT e.target_name, e.line, e.file_id
        FROM edges e
        JOIN files f ON f.id = e.file_id
        WHERE e.kind = 'import'
          AND e.synthesized_by IS NULL
          AND f.path NOT LIKE ':%'
        ORDER BY e.file_id, e.target_name, e.line
        """
    ).fetchall()

    candidates: list[_Candidate] = []
    for row in rows:
        file_id = int(row["file_id"])
        test_symbols = _test_symbols_in_file(symbols_by_file, file_id)
        if not test_symbols:
            continue
        target = str(row["target_name"])
        confidence = _confidence_for_target(target, production_symbols)
        if confidence is None:
            continue
        for symbol in test_symbols:
            if not _test_name_mentions_target(symbol.name, target):
                continue
            candidates.append(_Candidate(
                source=symbol.name,
                target=target,
                line=int(row["line"]),
                file_id=file_id,
                confidence=confidence,
                provenance="test-import",
            ))
    return candidates


def _target_from_test_name(name: str) -> str | None:
    final = name.rsplit(".", 1)[-1]
    if final.startswith("test_") and len(final) > len("test_"):
        return final.removeprefix("test_")
    if final.startswith("test") and len(final) > len("test"):
        candidate = final.removeprefix("test")
        return candidate[:1].lower() + candidate[1:] if candidate else None
    return None


def _collect_name_proximity_candidates(conn: sqlite3.Connection) -> list[_Candidate]:
    _test_symbols, production_symbols, symbols_by_file = _load_symbols(conn)
    candidates: list[_Candidate] = []
    for file_id, symbols in symbols_by_file.items():
        if not symbols or not is_test_file(symbols[0].path):
            continue
        for symbol in symbols:
            if not _is_test_symbol(symbol.name):
                continue
            target = _target_from_test_name(symbol.name)
            if target is None:
                continue
            confidence = _confidence_for_target(target, production_symbols)
            if confidence != "EXTRACTED":
                continue
            candidates.append(_Candidate(
                source=symbol.name,
                target=target,
                line=symbol.line,
                file_id=file_id,
                confidence="INFERRED",
                provenance="test-name-proximity",
            ))
    return candidates


def _dedupe(candidates: list[_Candidate]) -> list[_Candidate]:
    rank = {"EXTRACTED": 2, "AMBIGUOUS": 1, "INFERRED": 0}
    best: dict[tuple[str, str], _Candidate] = {}
    for candidate in candidates:
        key = (candidate.source, candidate.target)
        current = best.get(key)
        if current is None:
            best[key] = candidate
            continue
        if (
            _PROVENANCE_RANK.get(candidate.provenance, 0),
            rank[candidate.confidence],
            -candidate.line,
        ) > (
            _PROVENANCE_RANK.get(current.provenance, 0),
            rank[current.confidence],
            -current.line,
        ):
            best[key] = candidate
    return sorted(best.values(), key=lambda item: (item.source, item.target, item.line))


def index_test_edges(conn: sqlite3.Connection) -> int:
    """Refresh materialized `tests` edges from the current indexed graph.

    The pass runs after file indexing because only the complete symbol table can
    distinguish production targets from same-file test helpers. It never raises;
    failures leave test edges absent rather than aborting indexing.
    """
    try:
        edges = _dedupe([
            *_collect_exact_candidates(conn),
            *_collect_import_candidates(conn),
            *_collect_name_proximity_candidates(conn),
        ])
        with conn:
            conn.execute("DELETE FROM edges WHERE kind = 'tests'")
            if edges:
                conn.executemany(
                    """
                    INSERT INTO edges (
                        source_name, target_name, kind, file_id, line,
                        confidence, receiver, synthesized_by
                    )
                    VALUES (?, ?, 'tests', ?, ?, ?, NULL, ?)
                    """,
                    [
                        (
                            edge.source,
                            edge.target,
                            edge.file_id,
                            edge.line,
                            edge.confidence,
                            edge.provenance,
                        )
                        for edge in edges
                    ],
                )
        logger.debug("test_edges: wrote %d tests edge(s)", len(edges))
        return len(edges)
    except Exception as exc:  # noqa: BLE001 - post-pass must not abort indexing
        logger.debug("test_edges: failed to materialize test edges: %s", exc)
        return -1
