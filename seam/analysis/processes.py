"""Execution flows — entry points + forward call-chain expansion.

Answers "how does feature X work end-to-end?" — the comprehension question the
structural index can answer deterministically (no LLM, no embeddings).

Contract
--------
``list_entry_points(conn, *, limit, repo_root=None) -> list[EntryPoint]``

    Entry points = DEFINED, non-test symbols that are call-graph ROOTS (no incoming
    call edges), ranked by downstream reach (how many symbols they transitively
    call). On a real repo these surface exactly the program's starting points —
    CLI commands, web routes, MCP handlers, ``main`` — with zero heuristics on
    decorators or names (validated: decorators are often empty, and raw roots are
    too noisy; reach-ranking is the signal that works).

``build_flow(conn, entry, *, max_depth, max_breadth, repo_root=None) -> Flow | None``

    Forward expansion from ``entry`` over call/import edges into a depth- and
    breadth-capped, cycle-safe tree of steps. ``None`` if ``entry`` is unknown.

Both NEVER raise — return ``[]`` / ``None`` on any DB error.

Confidence note
---------------
Per-step confidence uses the fast name-count resolver (``confidence.resolve``),
NOT the Phase-5 import-promotion path. A flow is an *overview*; for promoted,
import-aware confidence use ``seam_impact`` / ``seam_trace``. This keeps flow
building to a few bulk queries instead of per-node resolution.

Layer: seam.analysis — imports stdlib + seam.config + seam.analysis.{confidence,
testpaths} only. No server/cli/query imports.
"""

import logging
import sqlite3
from collections import deque
from pathlib import Path
from typing import TypedDict

import seam.config as config
from seam.analysis.confidence import load_name_counts, resolve
from seam.analysis.testpaths import is_test_file

logger = logging.getLogger(__name__)

__all__ = ["EntryPoint", "FlowStep", "Flow", "list_entry_points", "build_flow"]


# ── Types ────────────────────────────────────────────────────────────────────


class EntryPoint(TypedDict):
    """A program entry point — a call-graph root ranked by downstream reach."""

    name: str
    kind: str | None
    file: str | None  # relativized when repo_root is provided
    reach: int  # distinct symbols reachable downstream (bounded by SEAM_FLOW_REACH_DEPTH)


class FlowStep(TypedDict):
    """One node in a flow tree (a symbol the entry point calls, directly or transitively).

    Fields:
        name        — symbol name
        kind        — symbol kind (function/method/class/…), None if undefined in index
        file        — declaring file (relativized when repo_root given), None if undefined
        line        — start line of the declaration, None if undefined
        confidence  — name-count confidence of the edge reaching this step
        children    — nested callees (empty at a leaf or a cut boundary)
        truncated   — True when this node's children were cut by depth/breadth caps
    """

    name: str
    kind: str | None
    file: str | None
    line: int | None
    confidence: str
    children: list["FlowStep"]
    truncated: bool


class Flow(TypedDict):
    """A full execution flow rooted at one entry point."""

    entry: str
    kind: str | None
    file: str | None
    steps: list[FlowStep]  # the entry point's direct callees, each with its own subtree
    total_steps: int  # count of every FlowStep node in the tree
    truncated: bool  # True if any cap was hit anywhere in the tree


# ── Internal loaders (one bulk query each — no per-node queries) ─────────────


def _load_adjacency(
    conn: sqlite3.Connection,
) -> tuple[dict[str, list[tuple[str, str]]], set[str]]:
    """Load the downstream call graph once.

    Returns (adjacency, all_targets):
        adjacency    — name -> sorted list of (callee_name, edge_kind), deduped.
        all_targets  — set of every target_name (used to detect roots: a root is a
                       source that is never a target).
    Self-edges (source == target) are excluded.
    """
    adjacency: dict[str, list[tuple[str, str]]] = {}
    all_targets: set[str] = set()
    seen: set[tuple[str, str, str]] = set()
    for row in conn.execute(
        "SELECT source_name, target_name, kind FROM edges WHERE source_name != target_name"
    ):
        src, tgt, kind = row["source_name"], row["target_name"], row["kind"]
        all_targets.add(tgt)
        dedup_key = (src, tgt, kind)
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        adjacency.setdefault(src, []).append((tgt, kind))
    for src in adjacency:
        adjacency[src].sort()  # deterministic callee order
    return adjacency, all_targets


def _load_meta(
    conn: sqlite3.Connection,
) -> dict[str, tuple[str | None, str | None, int | None]]:
    """name -> (kind, file_path, start_line) from the lowest-id defining row.

    Homonym-collapse: a name maps to its lowest-id definition, matching the
    name-keyed edges table and the rest of the read path.
    """
    meta: dict[str, tuple[str | None, str | None, int | None]] = {}
    for row in conn.execute(
        "SELECT s.name, s.kind, f.path, s.start_line "
        "FROM symbols s JOIN files f ON f.id = s.file_id ORDER BY s.id"
    ):
        name = row["name"]
        if name not in meta:  # lowest id wins (rows ordered by s.id)
            meta[name] = (row["kind"], row["path"], row["start_line"])
    return meta


def _rel(path: str | None, repo_root: Path | None) -> str | None:
    """Relativize a path to repo_root; pass through if not under root or root is None."""
    if path is None or repo_root is None:
        return path
    try:
        return str(Path(path).relative_to(repo_root))
    except ValueError:
        return path


def _reach(seed: str, adjacency: dict[str, list[tuple[str, str]]], max_depth: int) -> int:
    """Count distinct symbols reachable downstream from seed within max_depth hops.

    Bounded BFS — this is a RANKING signal for entry points, not a full walk.
    """
    seen: set[str] = set()
    queue: deque[tuple[str, int]] = deque([(seed, 0)])
    while queue:
        name, depth = queue.popleft()
        if depth >= max_depth:
            continue
        for target, _kind in adjacency.get(name, ()):
            if target not in seen:
                seen.add(target)
                queue.append((target, depth + 1))
    return len(seen)


def _count_steps(steps: list[FlowStep]) -> int:
    """Total FlowStep nodes in a tree (each node counts itself + its subtree)."""
    return sum(1 + _count_steps(s["children"]) for s in steps)


# ── Public interface ─────────────────────────────────────────────────────────


def list_entry_points(
    conn: sqlite3.Connection,
    *,
    limit: int = config.SEAM_FLOW_ENTRY_LIMIT,
    repo_root: Path | None = None,
) -> list[EntryPoint]:
    """Return the program's entry points: defined non-test roots ranked by reach.

    Args:
        conn:      Open SQLite connection (read-only).
        limit:     Max entry points to return.
        repo_root: When provided, entry-point file paths are relativized to it.

    Returns:
        list[EntryPoint] sorted by reach desc, then name. Empty list on any DB
        error or an index with no edges. NEVER raises.
    """
    try:
        adjacency, all_targets = _load_adjacency(conn)
        meta = _load_meta(conn)
    except sqlite3.Error:
        logger.debug("list_entry_points: DB error — returning []", exc_info=True)
        return []

    # Roots = defined symbols that are graph sources but never targets (uncalled),
    # excluding tests (a test runner is not a meaningful program entry point).
    roots = [
        name
        for name in adjacency
        if name not in all_targets and name in meta and not is_test_file(meta[name][1])
    ]

    scored = sorted(
        (
            EntryPoint(
                name=name,
                kind=meta[name][0],
                file=_rel(meta[name][1], repo_root),
                reach=_reach(name, adjacency, config.SEAM_FLOW_REACH_DEPTH),
            )
            for name in roots
        ),
        key=lambda e: (-e["reach"], e["name"]),
    )
    return scored[:limit]


def build_flow(
    conn: sqlite3.Connection,
    entry: str,
    *,
    max_depth: int = config.SEAM_FLOW_MAX_DEPTH,
    max_breadth: int = config.SEAM_FLOW_MAX_BREADTH,
    repo_root: Path | None = None,
) -> Flow | None:
    """Expand a single execution flow rooted at `entry`.

    Walks call/import edges forward from `entry`, building a depth/breadth-capped,
    cycle-safe tree. Each symbol appears at most once (first reach wins — keeps the
    tree readable and bounded, matching the homonym-collapse semantics elsewhere).

    Args:
        conn:        Open SQLite connection (read-only).
        entry:       Entry-point symbol name.
        max_depth:   Max levels of callees to expand (cut beyond → truncated=True).
        max_breadth: Max callees shown per node (excess dropped → truncated=True).
        repo_root:   When provided, file paths are relativized to it.

    Returns:
        A Flow tree, or None if `entry` is unknown (not a symbol and not in the
        call graph). NEVER raises.
    """
    try:
        adjacency, all_targets = _load_adjacency(conn)
        meta = _load_meta(conn)
        name_counts = load_name_counts(conn)
    except sqlite3.Error:
        logger.debug("build_flow(%r): DB error — returning None", entry, exc_info=True)
        return None

    # Unknown entry: not defined and absent from the graph entirely.
    if entry not in meta and entry not in adjacency and entry not in all_targets:
        return None

    any_truncated = False
    visited: set[str] = {entry}

    def expand(name: str, depth: int) -> tuple[list[FlowStep], bool]:
        """Return (child steps of `name`, whether `name`'s own children were cut)."""
        nonlocal any_truncated
        # Only unvisited callees (cycle + repeat-subtree safety).
        edges = [(t, k) for (t, k) in adjacency.get(name, []) if t not in visited]
        if not edges:
            return [], False
        # Children would sit at depth+1; cut entirely if that exceeds max_depth.
        if depth + 1 > max_depth:
            any_truncated = True
            return [], True
        node_truncated = False
        if len(edges) > max_breadth:
            node_truncated = True
            any_truncated = True
            edges = edges[:max_breadth]
        # Reserve all direct children BEFORE recursing so a callee appears once
        # (under its first parent) rather than duplicated across siblings.
        for target, _kind in edges:
            visited.add(target)
        steps: list[FlowStep] = []
        for target, _kind in edges:
            kind, path, line = meta.get(target, (None, None, None))
            child_steps, child_truncated = expand(target, depth + 1)
            steps.append(
                FlowStep(
                    name=target,
                    kind=kind,
                    file=_rel(path, repo_root),
                    line=line,
                    confidence=resolve(target, name_counts),
                    children=child_steps,
                    truncated=child_truncated,
                )
            )
        return steps, node_truncated

    steps, _root_truncated = expand(entry, 0)
    entry_kind, entry_path, _ = meta.get(entry, (None, None, None))
    return Flow(
        entry=entry,
        kind=entry_kind,
        file=_rel(entry_path, repo_root),
        steps=steps,
        total_steps=_count_steps(steps),
        truncated=any_truncated,
    )
