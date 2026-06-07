"""Whole-repository structure view — Tier D11.

Single public entry point:
    build_structure(conn, root) -> StructureResult

Builds a directory -> file -> container tree by joining symbols to files in a
single read query. The result carries:
  - dir nodes:       directories in the file-path hierarchy
  - file nodes:      indexed files under each directory
  - container nodes: class/interface/type symbols within each file
  - function nodes:  top-level functions (kind='function' with no '.' in name)
  - members:         method/member count rolled into the owning container node
                     (NOT emitted as separate nodes)

This is a PURE READ module — leaf, no seam deps beyond config.
It imports only stdlib + sqlite3. No server or CLI imports.

Slice 1 invariants:
  - truncated is always 0 (no per-directory or per-file caps applied).
  - The full tree is built for every indexed file.

Slice 2 — functional-area annotation:
  - file node area = label of the cluster with the PLURALITY of that file's
    symbols (via symbols.cluster_id -> clusters.label).
  - Tie-breaking: lowest cluster_id wins deterministically.
  - dir node area = plurality of its DIRECT child files' areas.
  - When no cluster data exists (cluster_id all NULL, or clusters table absent),
    area stays None for all nodes — graceful degradation, never an error.

Container detection (kind vocabulary normalizes to class/interface/type):
  - kind in {'class', 'interface', 'type'} -> container node.
  - kind == 'method'  -> rolled into parent container's `members` count.
  - A qualified name like 'Owner.member' -> method, regardless of stored kind.
  - kind == 'function' and name has no '.' -> top-level function node under file.

Path contract:
  - dir/file paths: relative to `root` (no absolute paths leak).
  - container paths: None (logical; no single declaring line shipped here).

Never raises on a partial/empty/garbage index — returns an empty but valid tree.
"""

import logging
import sqlite3
from pathlib import Path
from typing import TypedDict

logger = logging.getLogger(__name__)

# ── TypedDicts ────────────────────────────────────────────────────────────────


class StructureNode(TypedDict):
    """A single node in the structure tree.

    Fields:
        kind:         'dir' | 'file' | 'container' | 'function'
        name:         display name (dir basename, file name, symbol name)
        path:         repo-root-relative path string; None for container nodes
        symbol_count: total symbol rows in this subtree (file: row count in DB;
                      dir: sum of children; container: its own members + 1 for self)
        area:         functional-area label (Slice 1: always None)
        children:     child nodes (dirs/files under a dir; containers/funcs under a file)
        members:      count of method/member rows rolled into this container (0 for non-containers)
    """
    kind: str
    name: str
    path: str | None
    symbol_count: int
    area: str | None
    children: list  # list[StructureNode] — recursive, TypedDict can't self-ref cleanly
    members: int


class StructureResult(TypedDict):
    """Top-level result from build_structure().

    Fields:
        tree:      Root StructureNode (always a 'dir' node representing `root`).
        truncated: Count of nodes omitted by caps (Slice 1: always 0).
    """
    tree: StructureNode
    truncated: int


# ── Container kind detection ──────────────────────────────────────────────────

# Kinds stored in the DB that map to container nodes.
_CONTAINER_KINDS: frozenset[str] = frozenset({"class", "interface", "type"})

# Kinds stored in the DB that always map to methods/members (rolled up).
_METHOD_KINDS: frozenset[str] = frozenset({"method"})


def _is_container(kind: str) -> bool:
    """Return True when the symbol kind is a container (class/interface/type)."""
    return kind in _CONTAINER_KINDS


def _is_method_or_member(name: str, kind: str) -> bool:
    """Return True when the symbol should be rolled into a container's `members` count.

    Rules (in order):
    1. Stored kind is 'method' -> always a member.
    2. Name contains '.' (e.g. 'Owner.member') -> method, regardless of stored kind.
    WHY rule 2: extractors for some languages store method kind as 'function' with
    a qualified name like 'MyClass.do_something'. Seam's qualified_name contract is
    that methods carry 'Owner.method' qualified names. Using '.' as the heuristic
    here mirrors the convention used in graph_common and graph.py.
    """
    if kind in _METHOD_KINDS:
        return True
    # Bare name with a '.' separator -> qualified method name
    if "." in name:
        return True
    return False


# ── Query ─────────────────────────────────────────────────────────────────────


def _fetch_all_symbols(conn: sqlite3.Connection) -> list[tuple[str, str, str, int]]:
    """Fetch (file_path, name, kind, start_line) for all indexed symbols.

    Returns an empty list on any DB error (graceful degradation — never raises).
    The JOIN on files gives us the absolute file_path stored in the DB; callers
    relativize to root before building tree nodes.
    """
    try:
        rows = conn.execute(
            """
            SELECT f.path AS file_path, s.name, s.kind, s.start_line
            FROM symbols s
            JOIN files f ON f.id = s.file_id
            ORDER BY f.path, s.start_line
            """
        ).fetchall()
        return [(r["file_path"], r["name"], r["kind"], r["start_line"]) for r in rows]
    except Exception:
        logger.warning("build_structure: failed to fetch symbols; returning empty tree", exc_info=True)
        return []


# ── Cluster / area helpers ────────────────────────────────────────────────────


def _clusters_available(conn: sqlite3.Connection) -> bool:
    """Return True when the clusters table + symbols.cluster_id column both exist.

    WHY: pre-v4 indexes lack the clusters table; querying it would raise an error.
    We detect the absence and degrade to area=None on all nodes.
    """
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='clusters' LIMIT 1"
        ).fetchone()
        if row is None:
            return False
        # Check that symbols.cluster_id exists
        col_names = {r["name"] for r in conn.execute("PRAGMA table_info(symbols)").fetchall()}
        return "cluster_id" in col_names
    except Exception:
        return False


def _fetch_cluster_labels(conn: sqlite3.Connection) -> dict[int, str]:
    """Return {cluster_id: label} for all clusters.

    Returns an empty dict when no clusters exist or on any error.
    """
    try:
        rows = conn.execute("SELECT id, label FROM clusters").fetchall()
        return {r["id"]: r["label"] for r in rows}
    except Exception:
        return {}


def _fetch_file_cluster_counts(
    conn: sqlite3.Connection,
) -> dict[str, dict[int, int]]:
    """Return per-file cluster symbol counts.

    Shape: {abs_file_path: {cluster_id: symbol_count}}

    Only rows with a non-NULL cluster_id are included. Files whose symbols
    are entirely unclustered will be absent from the dict.

    Returns an empty dict on any DB error (graceful degradation).
    """
    try:
        rows = conn.execute(
            """
            SELECT f.path AS file_path, s.cluster_id, COUNT(*) AS cnt
            FROM symbols s
            JOIN files f ON f.id = s.file_id
            WHERE s.cluster_id IS NOT NULL
            GROUP BY f.path, s.cluster_id
            """
        ).fetchall()
        result: dict[str, dict[int, int]] = {}
        for r in rows:
            file_path: str = r["file_path"]
            cid: int = r["cluster_id"]
            cnt: int = r["cnt"]
            result.setdefault(file_path, {})[cid] = cnt
        return result
    except Exception:
        logger.warning(
            "build_structure: failed to fetch cluster counts; area will be None", exc_info=True
        )
        return {}


def _plurality_area(
    cluster_counts: dict[int, int],
    labels: dict[int, str],
) -> str | None:
    """Return the label of the cluster with the most symbols; None if no data.

    Tie-breaking: lowest cluster_id wins (deterministic).

    Args:
        cluster_counts: {cluster_id: symbol_count_in_this_file}
        labels:         {cluster_id: label} for all known clusters
    """
    if not cluster_counts:
        return None
    # Sort by (-count, cluster_id) so the highest count / lowest id comes first.
    best_cid = min(cluster_counts, key=lambda cid: (-cluster_counts[cid], cid))
    return labels.get(best_cid)


def _dir_plurality_area(child_areas: list[str | None]) -> str | None:
    """Return plurality area from a list of child file area strings.

    Only non-None areas are counted. Returns None when all areas are None.
    Tie-breaking: alphabetically first label (deterministic, stable).

    WHY alphabetic tie-break (not id): dir-level area is derived from
    file-level string labels; cluster ids are not directly accessible here.
    Alphabetic is reproducible and avoids a second DB lookup.
    """
    counts: dict[str, int] = {}
    for a in child_areas:
        if a is not None:
            counts[a] = counts.get(a, 0) + 1
    if not counts:
        return None
    # Sort by (-count, label) — most common, then alphabetically first on tie.
    return min(counts, key=lambda lbl: (-counts[lbl], lbl))


# ── Tree builder ──────────────────────────────────────────────────────────────


def _make_dir_node(name: str, path: str | None) -> StructureNode:
    """Create an empty 'dir' StructureNode."""
    return StructureNode(
        kind="dir",
        name=name,
        path=path,
        symbol_count=0,
        area=None,
        children=[],
        members=0,
    )


def _make_file_node(name: str, rel_path: str) -> StructureNode:
    """Create an empty 'file' StructureNode."""
    return StructureNode(
        kind="file",
        name=name,
        path=rel_path,
        symbol_count=0,
        area=None,
        children=[],
        members=0,
    )


def _make_container_node(name: str) -> StructureNode:
    """Create a 'container' StructureNode (class/interface/type)."""
    return StructureNode(
        kind="container",
        name=name,
        path=None,  # containers have no path — logical node
        symbol_count=1,  # counts itself; method roll-ups add to members
        area=None,
        children=[],
        members=0,
    )


def _make_function_node(name: str) -> StructureNode:
    """Create a 'function' StructureNode for a top-level function."""
    return StructureNode(
        kind="function",
        name=name,
        path=None,
        symbol_count=1,
        area=None,
        children=[],
        members=0,
    )


def _get_or_create_dir(
    parent_children: list,
    dir_name: str,
    dir_rel_path: str,
    dir_map: dict[str, StructureNode],
) -> StructureNode:
    """Return the existing dir child or create and append a new one.

    WHY dict cache: we may visit many files under the same subdir; O(1) lookup
    avoids scanning the children list each time.
    """
    if dir_rel_path in dir_map:
        return dir_map[dir_rel_path]
    node = _make_dir_node(dir_name, dir_rel_path)
    parent_children.append(node)
    dir_map[dir_rel_path] = node
    return node


def _build_file_tree(
    root: Path,
    symbols_by_file: dict[str, list[tuple[str, str, int]]],
    file_cluster_counts: dict[str, dict[int, int]],
    cluster_labels: dict[int, str],
) -> StructureNode:
    """Build the dir -> file -> container/function tree from the symbol map.

    Args:
        root:                 Project root (for path relativization).
        symbols_by_file:      {abs_file_path: [(name, kind, start_line), ...]}
        file_cluster_counts:  {abs_file_path: {cluster_id: symbol_count}}
                              (empty dict when cluster data is unavailable)
        cluster_labels:       {cluster_id: label} for area annotation

    Returns:
        Root StructureNode (kind='dir') representing `root`.
    """
    root_node = _make_dir_node(root.name or str(root), None)
    # dir_map: rel_path_str -> StructureNode, for fast lookup when inserting
    dir_map: dict[str, StructureNode] = {}

    for abs_file, syms in sorted(symbols_by_file.items()):
        # Compute relative path from repo root.
        try:
            rel_file = Path(abs_file).relative_to(root)
        except ValueError:
            # File outside root — use the absolute path as display name.
            rel_file = Path(abs_file)

        rel_file_str = str(rel_file)
        parts = rel_file.parts  # e.g. ('subdir', 'helper.py')

        # Navigate / create dir nodes for all parent directories.
        current_dir_node = root_node
        for i, part in enumerate(parts[:-1]):
            # Build the rel_path for this intermediate dir
            dir_rel = "/".join(parts[:i + 1])
            current_dir_node = _get_or_create_dir(
                current_dir_node["children"],
                part,
                dir_rel,
                dir_map,
            )

        # Create file node
        file_node = _make_file_node(parts[-1], rel_file_str)

        # Slice 2: annotate file node with its plurality functional area.
        # Uses the pre-fetched cluster counts to avoid per-file DB queries.
        file_counts = file_cluster_counts.get(abs_file, {})
        file_node["area"] = _plurality_area(file_counts, cluster_labels)

        # Partition symbols into containers, methods, and top-level functions.
        # containers: {container_name -> StructureNode}
        containers: dict[str, StructureNode] = {}

        for name, kind, _line in syms:
            if _is_container(kind):
                container_node = _make_container_node(name)
                containers[name] = container_node
                file_node["children"].append(container_node)
            elif _is_method_or_member(name, kind):
                # Determine owner: the part before the first '.'
                owner = name.split(".")[0] if "." in name else None
                if owner and owner in containers:
                    containers[owner]["members"] += 1
                    containers[owner]["symbol_count"] += 1
                # If owner not found (e.g. extracted before its class), count as orphan.
                # Still NOT added as a separate node — rolled up silently.
            else:
                # Top-level function (or other non-container, non-method kind)
                func_node = _make_function_node(name)
                file_node["children"].append(func_node)

        # symbol_count on the file node = total symbol rows (including methods)
        file_node["symbol_count"] = len(syms)

        current_dir_node["children"].append(file_node)

    # Propagate symbol_count upward: each dir's symbol_count = sum of its children.
    _propagate_symbol_counts(root_node)

    # Slice 2: propagate area upward through dir nodes (plurality of child file areas).
    _propagate_dir_areas(root_node)

    return root_node


def _propagate_symbol_counts(node: StructureNode) -> int:
    """Recursively compute and set symbol_count for dir nodes.

    Returns the node's symbol_count (post-propagation) for the parent's sum.
    File nodes already have their symbol_count set correctly; dir nodes get the
    sum of all direct children's symbol_counts.

    WHY bottom-up: we build children before parents; propagation must happen
    after the full subtree is assembled.
    """
    if node["kind"] == "dir":
        total = sum(_propagate_symbol_counts(child) for child in node["children"])
        node["symbol_count"] = total
    return node["symbol_count"]


def _propagate_dir_areas(node: StructureNode) -> str | None:
    """Recursively set area on dir nodes by collecting their children's areas.

    A dir's area = plurality of its DIRECT children's areas (file or nested dir).
    File nodes have their area already set; this pass only updates dir nodes.

    Returns the node's area (post-propagation) for the parent's collection.
    WHY bottom-up: same reasoning as _propagate_symbol_counts — children first.
    """
    if node["kind"] == "dir":
        # Recurse into children first, then collect their areas.
        child_areas: list[str | None] = []
        for child in node["children"]:
            child_areas.append(_propagate_dir_areas(child))
        node["area"] = _dir_plurality_area(child_areas)
    return node["area"]


# ── Public API ────────────────────────────────────────────────────────────────


def build_structure(conn: sqlite3.Connection, root: Path) -> StructureResult:
    """Build a directory -> file -> container/function structure tree.

    Reads the index in one JOIN query (files + symbols), then builds the tree
    in memory. Never raises — returns a safe empty tree on any error.

    Slice 2: Each file node is annotated with a functional 'area' label drawn
    from the Seam clustering data (clusters.label). Dir nodes get the plurality
    of their children's areas. When no cluster data exists (pre-v4 index or
    no clustering run), area stays None for all nodes.

    Args:
        conn: Open SQLite connection to the Seam index (read-only).
        root: Project root (Path) — used to relativize file paths.

    Returns:
        StructureResult with:
          tree:      Root 'dir' node representing `root`.
          truncated: Always 0 (no per-dir/file caps applied yet).
    """
    try:
        raw_symbols = _fetch_all_symbols(conn)
    except Exception:
        logger.warning("build_structure: _fetch_all_symbols raised", exc_info=True)
        raw_symbols = []

    # Group symbols by file path.
    # {abs_file_path: [(name, kind, start_line), ...]}
    symbols_by_file: dict[str, list[tuple[str, str, int]]] = {}
    for file_path, name, kind, start_line in raw_symbols:
        symbols_by_file.setdefault(file_path, []).append((name, kind, start_line))

    # Slice 2: fetch cluster data for area annotation.
    # Gracefully degrade to empty dicts when clusters are unavailable.
    cluster_labels: dict[int, str] = {}
    file_cluster_counts: dict[str, dict[int, int]] = {}
    if _clusters_available(conn):
        cluster_labels = _fetch_cluster_labels(conn)
        file_cluster_counts = _fetch_file_cluster_counts(conn)

    try:
        tree = _build_file_tree(root, symbols_by_file, file_cluster_counts, cluster_labels)
    except Exception:
        logger.warning("build_structure: _build_file_tree raised", exc_info=True)
        # Return a minimal valid tree rather than propagating the exception.
        tree = _make_dir_node(root.name or str(root), None)

    return StructureResult(tree=tree, truncated=0)
