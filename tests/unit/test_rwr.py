"""Unit tests for seam/analysis/rwr.py — pure personalized PageRank (RWR).

The function is pure (graph + seed set in → scores out): no DB, no IO, never raises.
Tests assert the relevance properties E3 relies on:
  - the seed set carries the most mass;
  - a neighbour woven into the seed's local neighbourhood outranks an equally-degree'd but
    topically-distant neighbour (the whole point of RWR over raw degree);
  - determinism;
  - degenerate inputs degrade to {} (never raise).
"""

from seam.analysis.rwr import personalized_pagerank


def _undirected(edges: list[tuple[str, str]]) -> dict[str, set[str]]:
    """Build a symmetric adjacency dict from an undirected edge list."""
    adj: dict[str, set[str]] = {}
    for a, b in edges:
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)
    return adj


def test_empty_adjacency_returns_empty() -> None:
    assert personalized_pagerank({}, {"S"}) == {}


def test_no_seed_in_graph_returns_empty() -> None:
    """If no seed is present in the graph, ranking cannot be personalized → {}."""
    adj = _undirected([("A", "B"), ("B", "C")])
    assert personalized_pagerank(adj, {"S"}) == {}


def test_isolated_seed_keeps_its_mass() -> None:
    """A seed present but with no edges holds (nearly) all the mass."""
    adj: dict[str, set[str]] = {"S": set()}
    scores = personalized_pagerank(adj, {"S"})
    assert scores["S"] > 0.99


def test_seed_outranks_neighbors_in_star() -> None:
    """In a star, the seed (restart target) scores higher than any leaf."""
    adj = _undirected([("S", "A"), ("S", "B"), ("S", "C")])
    scores = personalized_pagerank(adj, {"S"})
    assert scores["S"] > scores["A"]
    assert scores["A"] > 0
    # Symmetric leaves get equal scores.
    assert abs(scores["A"] - scores["B"]) < 1e-9
    assert abs(scores["B"] - scores["C"]) < 1e-9


def test_clustered_neighbor_outranks_distant_neighbor() -> None:
    """RWR's key property: a depth-1 neighbour reinforced by the seed's neighbourhood
    (triangle S-A-B) outranks a depth-1 neighbour that leads away on a chain (C-D-E),
    even though both A and C are exactly one hop from S. Raw degree cannot express this."""
    adj = _undirected([
        # Tight triangle around the seed: S, A, B mutually connected.
        ("S", "A"), ("S", "B"), ("A", "B"),
        # Chain leading away from the seed via C.
        ("S", "C"), ("C", "D"), ("D", "E"),
    ])
    scores = personalized_pagerank(adj, {"S"})
    # A (in the seed's triangle) is more relevant to S than C (gateway to a distant chain).
    assert scores["A"] > scores["C"], scores


def test_seed_set_personalizes_over_both_forms() -> None:
    """Seeding with {qualified, bare} forms personalizes the walk over both — a neighbour of
    the bare form is scored even when the qualified form is the 'canonical' seed."""
    adj = _undirected([
        ("Class.method", "callerQ"),   # neighbour of the qualified form
        ("method", "callerB"),         # neighbour of the bare form
    ])
    scores = personalized_pagerank(adj, {"Class.method", "method"})
    # Both seed forms and both their neighbours are scored (non-zero).
    assert scores["callerQ"] > 0
    assert scores["callerB"] > 0


def test_deterministic() -> None:
    """Same input → identical output across runs (load-bearing for the stable sort)."""
    adj = _undirected([("S", "A"), ("S", "B"), ("A", "B"), ("B", "C")])
    r1 = personalized_pagerank(adj, {"S"})
    r2 = personalized_pagerank(adj, {"S"})
    assert r1 == r2


def test_scores_sum_to_about_one() -> None:
    """Probability mass is conserved (dangling/isolated mass teleports back to the seed)."""
    adj = _undirected([("S", "A"), ("S", "B")])
    adj["X"] = set()  # an isolated extra node (dangling)
    scores = personalized_pagerank(adj, {"S"})
    assert abs(sum(scores.values()) - 1.0) < 1e-6


def test_unknown_neighbor_names_ignored() -> None:
    """Adjacency referencing a node that is not itself a key must not raise (it's ignored)."""
    adj = {"S": {"A", "ghost"}, "A": {"S"}}  # 'ghost' has no key
    scores = personalized_pagerank(adj, {"S"})
    assert "ghost" not in scores
    assert scores["S"] > 0 and scores["A"] > 0
