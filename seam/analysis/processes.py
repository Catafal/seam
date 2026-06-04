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
import re
import sqlite3
from collections import deque
from pathlib import Path
from typing import TypedDict

import seam.config as config
from seam.analysis.confidence import load_name_counts, resolve
from seam.analysis.testpaths import is_test_file

logger = logging.getLogger(__name__)

__all__ = [
    "EntryPoint",
    "FlowStep",
    "Flow",
    "list_entry_points",
    "build_flow",
    "compute_entry_score",
]


# ── P6b: framework entry-point scoring ───────────────────────────────────────
#
# A framework entry point (a Django view, a Flask/FastAPI route, a Go cmd/main,
# a Rails controller) often has a SHALLOW downstream reach — it delegates to one
# service call — yet it IS the program's true starting point. Raw-reach ranking
# buries it under deep internal utilities. We multiply reach by a framework-aware
# entry_score computed once at INDEX time from two cheap, language-agnostic signals:
#   (1) the file's PATH — conventional locations of request handlers / entry points;
#   (2) the symbol's DECORATOR text — route/handler decorators across frameworks.
# The score is a small multiplier (>=1.0); 1.0 is the neutral baseline so a symbol
# with no signal ranks exactly as before (byte-identical when nothing matches).

# Path substrings → multiplier. Checked case-insensitively against the POSIX path.
# Ordered roughly by specificity; the HIGHEST matching multiplier wins (max, not sum)
# so a file under both 'app/api/' and 'routes/' is not double-counted.
_PATH_PATTERNS: tuple[tuple[str, float], ...] = (
    ("/pages/", 1.8),          # Next.js / SvelteKit file-system routes
    ("/app/api/", 1.8),        # Next.js app-router API handlers
    ("/routes/", 1.7),         # generic web routes dir
    ("/controllers/", 1.7),    # Rails / Spring / NestJS controllers
    ("/handlers/", 1.6),       # Go / serverless handlers
    ("/views/", 1.6),          # Django/Flask views package
    ("views.py", 1.7),         # Django views module
    ("urls.py", 1.5),          # Django URL conf (entry registration)
    ("routes.py", 1.7),        # Flask/FastAPI routes module
    ("/endpoints/", 1.6),      # FastAPI endpoints package
    ("/api/", 1.5),            # generic api dir
    ("/cmd/", 1.6),            # Go command entry points
    ("/commands/", 1.5),       # CLI command modules
    ("/cli/", 1.4),            # CLI package
    ("main.go", 1.6),          # Go main
    ("main.py", 1.5),          # Python main module
    ("main.rs", 1.5),          # Rust main
    ("app.py", 1.4),           # Flask app entry
    ("server.py", 1.4),        # server entry module
    ("/resolvers/", 1.5),      # GraphQL resolvers
    ("index.ts", 1.3),         # TS package entry / route file
)

# Decorator substrings → multiplier. Matched case-insensitively against the joined
# decorator text. Covers the common web/CLI frameworks across Python/TS/Java/etc.
_DECORATOR_PATTERNS: tuple[tuple[str, float], ...] = (
    (".route", 1.8),           # @app.route (Flask), @router.route
    ("@router.", 1.8),         # @router.get/post/... (FastAPI)
    ("@app.", 1.7),            # @app.get/post/... (FastAPI), @app.command (Typer)
    (".command", 1.6),         # @app.command (Typer), @cli.command (Click)
    ("getmapping", 1.7),       # @GetMapping (Spring)
    ("postmapping", 1.7),      # @PostMapping (Spring)
    ("requestmapping", 1.7),   # @RequestMapping (Spring)
    ("restcontroller", 1.7),   # @RestController (Spring)
    ("@controller", 1.6),      # @Controller (Spring/NestJS)
    ("@get(", 1.6),            # @Get() (NestJS)
    ("@post(", 1.6),           # @Post() (NestJS)
    ("@api_view", 1.6),        # @api_view (Django REST framework)
    ("@task", 1.4),            # @task (Celery) — background entry point
)


def compute_entry_score(
    file_path: str | None, decorators: list[str] | None
) -> float:
    """Framework-aware entry-point multiplier for one symbol (computed at index time).

    Returns the MAX matching multiplier across the file-path patterns and the
    decorator patterns, defaulting to the neutral baseline 1.0 when nothing
    matches (so a non-entry symbol ranks exactly as raw reach would).

    Pure + defensive: NEVER raises. Bad input (None / wrong type) → 1.0.

    Args:
        file_path:  Declaring file path (any OS form; matched as POSIX, lowercased).
        decorators: Symbol decorator strings (e.g. ['@app.route("/x")']); may be None.
    """
    score = 1.0
    try:
        if isinstance(file_path, str) and file_path:
            posix = file_path.replace("\\", "/").lower()
            for pattern, mult in _PATH_PATTERNS:
                if pattern in posix and mult > score:
                    score = mult
        if isinstance(decorators, list) and decorators:
            text = " ".join(d for d in decorators if isinstance(d, str)).lower()
            text = re.sub(r"\s+", "", text)  # collapse whitespace for robust substring match
            for pattern, mult in _DECORATOR_PATTERNS:
                if pattern in text and mult > score:
                    score = mult
    except Exception:  # noqa: BLE001 — scoring must never break indexing
        logger.debug("compute_entry_score failed for %r — using baseline", file_path)
        return 1.0
    return score


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


def _load_entry_scores(conn: sqlite3.Connection) -> dict[str, float]:
    """name -> entry_score from the lowest-id defining row (homonym-collapse).

    Mirrors _load_meta's lowest-id-wins rule so a name's score matches the
    definition the rest of the read path uses. NULL (pre-P6b or un-reindexed
    rows) → 1.0 neutral baseline, so missing scores never penalise ranking.

    Returns {} when the entry_score column is absent (pre-v9 index) — callers
    then fall back to the baseline for every name.
    """
    scores: dict[str, float] = {}
    try:
        for row in conn.execute(
            "SELECT name, entry_score FROM symbols ORDER BY id"
        ):
            name = row["name"]
            if name not in scores:  # lowest id wins
                scores[name] = row["entry_score"] if row["entry_score"] is not None else 1.0
    except sqlite3.Error:
        # entry_score column absent (pre-v9) — return empty so callers use 1.0.
        logger.debug("_load_entry_scores: column absent or DB error — baseline", exc_info=True)
        return {}
    return scores


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
        entry_scores = _load_entry_scores(conn)
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

    # P6b: rank by framework-aware weighted reach (entry_score * reach) instead of
    # raw reach, so a low-reach route/view/controller outranks a deep utility.
    # entry_score is pre-computed at INDEX time (one column read, no BFS re-run).
    # SEAM_ENTRY_SCORE=off → every weight is the baseline 1.0 → byte-identical to
    # raw-reach ranking. The 'reach' field stays the RAW reach (the multiplier is a
    # ranking signal only — callers still see the true downstream count).
    use_scores = config.SEAM_ENTRY_SCORE == "on"

    def _weight(name: str, reach: int) -> float:
        score = entry_scores.get(name, 1.0) if use_scores else 1.0
        return score * reach

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
        key=lambda e: (-_weight(e["name"], e["reach"]), e["name"]),
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
