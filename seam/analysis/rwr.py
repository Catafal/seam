"""Pure personalized-PageRank (Random-Walk-with-Restart) module — E3 neighbor ranking.

Public API:
    personalized_pagerank(adjacency, seeds, *, restart, iters, tol) -> dict[str, float]

This is a DEEP PURE module: graph + seed set in → relevance scores out.
No SQLite, no file I/O, no config, no side effects (mirrors clustering.py).

`seeds` is a SET because a method symbol stored as "Class.method" can have call edges keyed under
the bare "method" (Seam's qualified/bare asymmetry). Personalizing the walk over BOTH forms (the
symbol's edge_match_names) ensures neighbours reachable via either form are scored relative to the
same logical seed.

WHY RWR for neighbor ranking (vs. raw degree):
    Ranking a symbol's 1-hop neighbors by how RELEVANT they are TO THAT SYMBOL needs more than
    degree centrality. RWR restarts the walk at the seed every step with probability `restart`,
    so mass concentrates on nodes the seed actually reaches and re-reaches. A neighbor woven into
    the seed's local neighborhood (it shares callers/callees with the seed → same functional
    cluster) accumulates more score than a globally-popular but topically-distant neighbor. That
    "closeness to the seed" is exactly what a context bundle wants to keep when it must cap.

Algorithm (power-iterated personalized PageRank):
    r_{k+1}[n] = restart * teleport[n] + (1 - restart) * Σ_{m: n ∈ adj[m]} r_k[m] / outdeg(m)
    where teleport is all mass on `seed` (personalization). Dangling/isolated mass (a node with
    no neighbors) is sent back to the seed so probability is conserved. Undirected adjacency is
    assumed (the caller symmetrizes), matching CodeGraph's computeGraphRelevance.

Determinism is load-bearing: nodes are processed in sorted order and the math is exact-rational-
free float arithmetic with a fixed iteration cap, so the same graph always yields the same scores.

Degenerate inputs (never raises):
    - empty adjacency                          -> {}
    - no seed present in adjacency             -> {} (cannot personalize → caller skips ranking)
    - seeds present but isolated (no edges)    -> all mass on the seed set
"""

import logging

logger = logging.getLogger(__name__)

# Module constants (implementation details, not user-facing knobs):
# restart 0.15 = the canonical PageRank teleport probability; the exact value is not sensitive
# for RANKING (only the relative order of scores matters). 30 iterations + 1e-6 L1 tolerance
# converge comfortably on the bounded subgraphs E3 feeds in (~hundreds of nodes).
_DEFAULT_RESTART = 0.15
_DEFAULT_ITERS = 30
_DEFAULT_TOL = 1e-6


def personalized_pagerank(
    adjacency: dict[str, set[str]],
    seeds: set[str],
    *,
    restart: float = _DEFAULT_RESTART,
    iters: int = _DEFAULT_ITERS,
    tol: float = _DEFAULT_TOL,
) -> dict[str, float]:
    """Personalized PageRank (RWR) scores for every node, personalized to the `seeds` set.

    Args:
        adjacency: node name -> set of neighbor names. Assumed UNDIRECTED (caller symmetrizes).
                   Neighbor names not present as keys are ignored (dangling targets).
        seeds:     the nodes the walk restarts at (the symbol's edge_match_names — qualified +
                   bare). Teleport mass is split uniformly across the seeds present in the graph.
        restart:   teleport-to-seeds probability per step (0 < restart < 1).
        iters:     max power iterations.
        tol:       L1-convergence threshold; iteration stops early once the score vector moves
                   less than this between steps.

    Returns:
        dict node -> score (scores sum to ~1.0). Higher = more relevant to the seed set.
        Returns {} when no seed is present in the graph (caller then skips ranking). Never raises.
    """
    try:
        # Restrict to nodes that are keys; drop edges to unknown nodes so out-degree and the
        # back-distribution stay consistent (mirrors clustering.py's "edges referencing unknown
        # nodes are safely ignored").
        nodes = sorted(adjacency.keys())
        if not nodes:
            return {}
        node_set = set(nodes)
        # Symmetric, self-loop-free neighbor lists restricted to known nodes.
        nbrs: dict[str, list[str]] = {
            n: sorted({m for m in adjacency.get(n, ()) if m in node_set and m != n})
            for n in nodes
        }

        # Teleport distribution: uniform over the seeds that actually exist in the graph.
        present_seeds = sorted(s for s in seeds if s in node_set)
        if not present_seeds:
            return {}  # cannot personalize → caller falls back to min_id order
        teleport: dict[str, float] = {n: 0.0 for n in nodes}
        seed_share = 1.0 / len(present_seeds)
        for s in present_seeds:
            teleport[s] = seed_share

        # Start all mass on the seed set (teleport vector).
        rank: dict[str, float] = dict(teleport)
        walk = 1.0 - restart

        for _ in range(iters):
            nxt: dict[str, float] = {n: 0.0 for n in nodes}
            dangling_mass = 0.0
            # Push each node's current mass to its neighbors (walk component).
            for n in nodes:
                deg = len(nbrs[n])
                if deg == 0:
                    # Isolated/dangling node: its walk mass has nowhere to go → redirect to the
                    # seed set below (conserves total probability instead of leaking it).
                    dangling_mass += rank[n]
                    continue
                share = rank[n] / deg
                for m in nbrs[n]:
                    nxt[m] += share
            # Apply the (1 - restart) walk weight, then add the restart teleport + the dangling
            # mass, both distributed over the seed set.
            for n in nodes:
                nxt[n] = walk * nxt[n] + (restart + walk * dangling_mass) * teleport[n]

            # L1 convergence check.
            delta = sum(abs(nxt[n] - rank[n]) for n in nodes)
            rank = nxt
            if delta < tol:
                break

        return rank
    except Exception:  # noqa: BLE001 — pure module must never raise; degrade to no-ranking.
        logger.debug("personalized_pagerank: degraded for seeds=%r", seeds, exc_info=True)
        return {}
