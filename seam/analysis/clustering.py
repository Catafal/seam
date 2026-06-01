"""Pure community detection module — Louvain greedy modularity maximization.

Public API:
    detect_communities(nodes, edges) -> dict[str, int]

This is a DEEP PURE module: graph in → cluster map out.
No SQLite, no file I/O, no config, no side effects.

Design decisions:
    - Pure-Python Louvain (greedy modularity). No external deps.
    - Determinism is load-bearing: nodes are sorted before processing,
      and tie-breaking is always deterministic (sort by community label).
    - Singleton/disconnected nodes each get their own cluster ID.
    - Self-loops are ignored (they don't affect modularity).
    - Edges referencing unknown nodes are safely ignored.
    - Empty graph → {}.
    - Never raises on any input — returns sensible degraded output.

Algorithm (standard Louvain phase 1 — modularity maximization):
    1. Assign each node to its own singleton community.
    2. For each node (in sorted order), try moving it to each neighbor's
       community. Accept the move with the highest positive modularity gain.
    3. Repeat until no node moves in a full pass (convergence) or
       _MAX_ITERATIONS is reached.
    4. Renumber community IDs as stable integers (sorted by min member name).
"""

import logging

logger = logging.getLogger(__name__)

# Maximum passes over all nodes. Prevents infinite loops on pathological graphs.
_MAX_ITERATIONS = 100


def detect_communities(
    nodes: list[str],
    edges: list[tuple[str, str]],
) -> dict[str, int]:
    """Detect communities in an undirected graph using Louvain modularity optimization.

    Args:
        nodes: List of node names (symbol names). Duplicates are deduplicated.
        edges: List of (source, target) pairs. Directed edges are treated as
               undirected (both directions considered). Edges to unknown nodes
               are silently ignored.

    Returns:
        Dict mapping each node name to an integer cluster ID.
        Empty dict when nodes is empty.
        Cluster IDs are stable integers starting at 0, ordered by the
        lexicographically smallest member of each community (deterministic).

    Never raises. On any internal error, logs a warning and returns
    each node in its own singleton cluster.
    """
    if not nodes:
        return {}

    try:
        return _detect_communities_impl(nodes, edges)
    except Exception as exc:
        logger.warning(
            "clustering: detect_communities failed with %s — falling back to singletons",
            exc,
        )
        # Deterministic fallback: each node its own cluster (sorted for stability)
        sorted_unique = sorted(set(nodes))
        return {n: i for i, n in enumerate(sorted_unique)}


def _detect_communities_impl(
    nodes: list[str],
    edges: list[tuple[str, str]],
) -> dict[str, int]:
    """Inner implementation — may raise; outer function catches and degrades."""
    # Deduplicate and sort nodes for deterministic processing order
    unique_nodes = sorted(set(nodes))

    if not unique_nodes:
        return {}

    # Build undirected adjacency sets — only between known nodes
    node_set = set(unique_nodes)
    adj: dict[str, set[str]] = {n: set() for n in unique_nodes}

    for src, tgt in edges:
        if src == tgt:
            # Self-loops don't affect modularity — skip
            continue
        if src in node_set and tgt in node_set:
            adj[src].add(tgt)
            adj[tgt].add(src)

    # m = total number of undirected edges (used in modularity formula)
    # Count half-sum of all degrees (each undirected edge appears twice in degree sum)
    degree: dict[str, int] = {n: len(adj[n]) for n in unique_nodes}
    m = sum(degree.values()) // 2  # undirected edge count

    # Each node starts in its own singleton community (labeled by the node name)
    # Using node names as community labels ensures determinism: no integer IDs
    # that depend on insertion order.
    community: dict[str, str] = {n: n for n in unique_nodes}

    # community_degree[comm]: sum of degrees of all nodes in community comm
    community_degree: dict[str, int] = {n: degree[n] for n in unique_nodes}

    if m == 0:
        # No edges → every node is its own singleton; assign stable int IDs
        return _assign_stable_ids(community, unique_nodes)

    # Louvain phase 1: iterate until no node moves
    for _iteration in range(_MAX_ITERATIONS):
        improved = False

        # Process nodes in deterministic sorted order
        for node in unique_nodes:
            current_comm = community[node]
            node_deg = degree[node]

            # Count edges from this node into each neighboring community
            # k_in[comm] = number of edges from node to members of comm
            k_in: dict[str, int] = {}
            for neighbor in adj[node]:
                nc = community[neighbor]
                k_in[nc] = k_in.get(nc, 0) + 1

            # Remove node from current community (to evaluate re-insertion)
            community_degree[current_comm] -= node_deg
            k_in_current = k_in.get(current_comm, 0)

            # Find best community using Louvain modularity gain formula:
            #   ΔQ ∝ k_in_C - (Σ_tot * k_i) / (2m)
            # We compare against the "remove" baseline (k_in=0, sigma_tot=0),
            # so the "stay" option is evaluated fairly.
            # WHY: Using 2*m as denominator normalization for the degree term.
            best_comm = current_comm
            # Gain of reinserting into the current community after removal
            best_gain = _modularity_gain(k_in_current, community_degree[current_comm], node_deg, m)

            # Evaluate all neighbor communities (sorted for deterministic tie-breaking)
            for cand_comm in sorted(k_in.keys()):
                if cand_comm == current_comm:
                    continue
                gain = _modularity_gain(k_in[cand_comm], community_degree[cand_comm], node_deg, m)
                # Accept strictly better gain; on equal gain, prefer smaller community label
                # (lexicographic tie-break ensures determinism)
                if gain > best_gain or (gain == best_gain and cand_comm < best_comm):
                    best_gain = gain
                    best_comm = cand_comm

            # Reinsert node into best community (may be the same as current)
            community[node] = best_comm
            community_degree[best_comm] += node_deg

            if best_comm != current_comm:
                improved = True

        if not improved:
            break

    return _assign_stable_ids(community, unique_nodes)


def _modularity_gain(k_in: int, sigma_tot: int, k_i: int, m: int) -> float:
    """Compute the modularity gain of inserting a node into a community.

    Formula: ΔQ = k_in/m - (sigma_tot * k_i) / (2 * m^2)

    Args:
        k_in: Number of edges from the node to existing community members.
        sigma_tot: Total degree of the community (after removing the node).
        k_i: Degree of the node being placed.
        m: Total number of undirected edges in the graph.

    WHY: This is the standard Louvain gain formula from Blondel et al. (2008).
    We use floating point division to handle fractional gains correctly.
    """
    if m == 0:
        return 0.0
    return float(k_in) / m - (sigma_tot * k_i) / (2.0 * m * m)


def _assign_stable_ids(
    community: dict[str, str],
    sorted_nodes: list[str],
) -> dict[str, int]:
    """Assign stable integer IDs to communities.

    WHY: Internal community labels (node name strings) are arbitrary identifiers
    used during the algorithm. We convert them to stable integers by sorting
    communities by their lexicographically smallest member — so the same logical
    partition always gets the same integer IDs.

    Args:
        community: Mapping of node -> community label (internal string).
        sorted_nodes: All nodes in sorted order.

    Returns:
        Dict of node -> integer cluster ID starting at 0.
    """
    # Collect the minimum member name per community (for sorting)
    comm_min_member: dict[str, str] = {}
    for node in sorted_nodes:
        comm_label = community[node]
        if comm_label not in comm_min_member:
            comm_min_member[comm_label] = node
        else:
            if node < comm_min_member[comm_label]:
                comm_min_member[comm_label] = node

    # Sort community labels by their minimum member → deterministic integer IDs
    sorted_comm_labels = sorted(comm_min_member.keys(), key=lambda c: comm_min_member[c])
    comm_to_int: dict[str, int] = {label: i for i, label in enumerate(sorted_comm_labels)}

    return {node: comm_to_int[community[node]] for node in sorted_nodes}
