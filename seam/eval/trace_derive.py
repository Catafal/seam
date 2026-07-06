"""Pure derive functions for the agent-trace-derived eval goldens loop (WS6.1).

EVAL MODULE — stdlib-only imports. No new runtime dependency.
Pure functions: no IO, no config, no DB inside the derive functions.
Never raises: all public functions catch exceptions and return a safe sentinel.

This module is the analytical heart of the trace-capture loop:

  derive_outcome_from_diff(diff, symbols) → set[str]
    Maps a unified git diff's changed hunks to the qualified symbol names whose
    file+line ranges they intersect. This is the "hindsight outcome signal" —
    the set of symbols the agent actually edited during the session.

  derive_goldens(trace_records, outcome) → list[GoldenCandidate]
    For each unique (tool, query) in the trace records, emits a GoldenCandidate
    with expected_symbols = outcome symbols, gap=True when an outcome symbol was
    absent from that query's captured result. Deduplicates by (tool, query),
    merging session_ids from all contributing records.

Design decisions:
  - GoldenCandidate shape mirrors the golden.json recall query shape so approved
    candidates can be run through the existing recall_harness metric unchanged.
  - derive_goldens receives plain dicts (the NDJSON trace records) so it has no
    dependency on the trace_capture module — it is independently testable.
  - The query is extracted from trace record's args dict heuristically (tries
    "query", "concept", "symbol" in order) to handle all tool arg shapes.
  - gap=True when ANY outcome symbol is absent from that query's symbol_names.
    (If ALL outcome symbols were found, gap=False — the retrieval worked.)
  - k defaults to len(symbol_names) from the first record for the (tool, query)
    pair, capped at the default recall harness k=10 if that is larger.

Never raises: degrades to [] (derive_goldens) or set() (derive_outcome_from_diff)
on any unexpected input.
"""

from __future__ import annotations

import datetime
import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# Default k for GoldenCandidate when k cannot be inferred from the trace record.
_DEFAULT_K: int = 10

# Unified diff hunk header pattern: @@ -old_start,old_count +new_start,new_count @@
# We use the NEW file coordinates (the side the agent edited).
_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")

# Unified diff file-header pattern: +++ b/<path>
_FILE_HEADER_RE = re.compile(r"^\+\+\+ b/(.+)$")


# ── Data structures ───────────────────────────────────────────────────────────


@dataclass
class Provenance:
    """Audit trail for a GoldenCandidate — where it came from."""

    session_ids: list[str]    # All session IDs that contributed to this candidate.
    source_query: str          # The query text that produced this candidate.
    derived_at: str            # ISO-format timestamp when derive_goldens was called.

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_ids": list(self.session_ids),
            "source_query": self.source_query,
            "derived_at": self.derived_at,
        }


@dataclass
class GoldenCandidate:
    """A derived golden candidate in the recall golden.json shape.

    Mirrors the golden.json query format so approved candidates can be run
    through the existing recall_harness / recall@K + MRR metric unchanged.

    Fields:
        tool:             The Seam tool that produced the trace record.
        query:            The query text (from the trace record's args).
        k:                The cutoff k for the recall metric.
        expected_symbols: The outcome symbols (the agent's hindsight edits).
        gap:              True when ANY expected symbol was absent from the result.
        provenance:       Audit trail (session_ids, source_query, derived_at).
    """

    tool: str
    query: str
    k: int
    expected_symbols: list[str]
    gap: bool
    provenance: Provenance

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "query": self.query,
            "k": self.k,
            "expected_symbols": list(self.expected_symbols),
            "gap": self.gap,
            "provenance": self.provenance.to_dict(),
        }


# ── Internal accumulator for dedup logic ──────────────────────────────────────


@dataclass
class _Accumulator:
    """In-progress accumulation of records sharing the same (tool, query) key."""

    tool: str
    query: str
    session_ids: list[str] = field(default_factory=list)
    result_symbol_sets: list[set[str]] = field(default_factory=list)
    first_k: int = _DEFAULT_K


# ── Public API ────────────────────────────────────────────────────────────────


def derive_goldens(
    trace_records: list[dict[str, Any]],
    outcome: set[str],
) -> list[GoldenCandidate]:
    """Derive golden candidates from a list of trace records and an outcome symbol set.

    For each unique (tool, query) pair in trace_records, emits one GoldenCandidate with:
      - expected_symbols = the outcome symbols (the hindsight relevant set)
      - gap = True when ANY outcome symbol was absent from that query's result

    Deduplicates by (tool, query), merging session_ids from all contributing records.
    Empty outcome → [] (not an error; a read-only session has no outcome signal).
    Empty records → [].
    Never raises.

    Args:
        trace_records: List of trace record dicts from the NDJSON trace file.
                       Each dict must have: tool, args, symbol_names, session_id.
        outcome:       Set of qualified symbol names the agent actually edited.

    Returns:
        List of GoldenCandidate objects, one per unique (tool, query).
    """
    if not outcome or not trace_records:
        return []

    try:
        now_iso = datetime.datetime.now(datetime.UTC).isoformat()
        # Accumulate records by (tool, query) key.
        accum: dict[tuple[str, str], _Accumulator] = {}

        for rec in trace_records:
            try:
                tool = str(rec.get("tool", ""))
                if not tool:
                    continue
                query = _extract_query(rec)
                if query is None:
                    continue
                key = (tool, query)
                if key not in accum:
                    result_k = len(rec.get("symbol_names") or [])
                    accum[key] = _Accumulator(
                        tool=tool,
                        query=query,
                        first_k=max(result_k, _DEFAULT_K),
                    )
                acc = accum[key]
                session_id = str(rec.get("session_id", ""))
                if session_id and session_id not in acc.session_ids:
                    acc.session_ids.append(session_id)
                symbol_names = rec.get("symbol_names") or []
                acc.result_symbol_sets.append(set(symbol_names))
            except Exception:  # noqa: BLE001
                logger.warning("trace_derive: skipping malformed record", exc_info=True)
                continue

        # Build candidates from accumulators.
        candidates: list[GoldenCandidate] = []
        expected = sorted(outcome)  # stable sorted list from the set

        for (tool, query), acc in accum.items():
            # The combined result is the UNION of all result symbol sets for this
            # (tool, query) across sessions (we ask: did this query EVER find the symbol?)
            combined_result: set[str] = set()
            for s in acc.result_symbol_sets:
                combined_result |= s

            # gap=True when ANY outcome symbol was NOT found in the combined result.
            gap = not outcome.issubset(combined_result)

            prov = Provenance(
                session_ids=list(acc.session_ids),
                source_query=query,
                derived_at=now_iso,
            )
            candidates.append(
                GoldenCandidate(
                    tool=tool,
                    query=query,
                    k=acc.first_k,
                    expected_symbols=expected,
                    gap=gap,
                    provenance=prov,
                )
            )

        return candidates

    except Exception:  # noqa: BLE001
        logger.warning("trace_derive: derive_goldens failed", exc_info=True)
        return []


def derive_outcome_from_diff(
    diff: str,
    symbols: list[dict[str, Any]],
) -> set[str]:
    """Map a unified git diff to the qualified symbol names whose ranges the hunks intersect.

    The diff is the hindsight outcome signal: the set of symbols the agent actually edited.
    The mapping reuses the index's existing symbol file+line ranges — no re-parsing.

    Args:
        diff:    A unified diff string (output of `git diff`).
        symbols: List of symbol index rows, each with:
                   - name: str (qualified symbol name)
                   - file_path: str (relative to repo root)
                   - start_line: int
                   - end_line: int

    Returns:
        Set of qualified symbol names whose [start_line, end_line] range intersects
        at least one changed hunk in the diff for the same file. Empty on any error.
    """
    if not diff or not symbols:
        return set()

    try:
        # Build a file → list of (start_line, end_line, name) index for fast lookup.
        file_index: dict[str, list[tuple[int, int, str]]] = {}
        for sym in symbols:
            fp = sym.get("file_path", "")
            name = sym.get("name", "")
            sl = sym.get("start_line")
            el = sym.get("end_line")
            if not fp or not name or sl is None or el is None:
                continue
            file_index.setdefault(fp, []).append((int(sl), int(el), name))

        # Parse the diff and collect changed line ranges per file.
        result: set[str] = set()
        current_file: str | None = None

        for raw_line in diff.splitlines():
            # New file header
            m_file = _FILE_HEADER_RE.match(raw_line)
            if m_file:
                current_file = m_file.group(1).strip()
                continue

            # Hunk header
            m_hunk = _HUNK_RE.match(raw_line)
            if m_hunk and current_file is not None:
                new_start = int(m_hunk.group(1))
                n_str = m_hunk.group(2)
                n_lines = int(n_str) if n_str is not None else 1
                hunk_end = new_start + max(n_lines - 1, 0)

                # Find symbols in the current file whose range overlaps this hunk.
                for sym_start, sym_end, sym_name in file_index.get(current_file, []):
                    # Overlap: hunk_start <= sym_end AND hunk_end >= sym_start
                    if new_start <= sym_end and hunk_end >= sym_start:
                        result.add(sym_name)

        return result

    except Exception:  # noqa: BLE001
        logger.warning("trace_derive: derive_outcome_from_diff failed", exc_info=True)
        return set()


# ── Internal helpers ──────────────────────────────────────────────────────────


def _extract_query(record: dict[str, Any]) -> str | None:
    """Extract the query text from a trace record's args dict.

    Tries "query" (seam_search), "concept" (seam_query), "symbol" (seam_context/impact)
    in order. Returns None if no query can be extracted (malformed record).
    """
    args = record.get("args")
    if not isinstance(args, dict):
        return None
    for key in ("query", "concept", "symbol"):
        val = args.get(key)
        if val and isinstance(val, str):
            return val
    return None
