"""Tests for seam/analysis/clustering.py — pure community detection module.

TDD: Tests written before implementation. Each class maps to one behavioral slice.

Test groups:
    D1 — Two-community graph → exactly two clusters (and determinism)
    D2 — Disconnected components → separate clusters
    D3 — Single node → its own cluster
    D4 — Empty graph → empty map
    D5 — Degenerate input never raises
    D6 — Determinism: same input → byte-identical output across two runs
"""

import pytest

# ── D1: Two-community graph ───────────────────────────────────────────────────


class TestTwoCommunityGraph:
    """D1: A graph with two clear communities detects exactly two clusters."""

    def _make_two_community_input(self):
        """Build a small graph with two tight communities connected by a single bridge.

        Community A: a1, a2, a3 (all fully connected)
        Community B: b1, b2, b3 (all fully connected)
        Bridge: a1 -- b1 (single weak link)
        """
        nodes = ["a1", "a2", "a3", "b1", "b2", "b3"]
        edges = [
            # Community A (dense)
            ("a1", "a2"),
            ("a2", "a3"),
            ("a1", "a3"),
            # Community B (dense)
            ("b1", "b2"),
            ("b2", "b3"),
            ("b1", "b3"),
            # Bridge
            ("a1", "b1"),
        ]
        return nodes, edges

    def test_two_community_graph_produces_two_clusters(self) -> None:
        """A 2-community graph should produce exactly 2 distinct cluster IDs."""
        from seam.analysis.clustering import detect_communities

        nodes, edges = self._make_two_community_input()
        result = detect_communities(nodes, edges)

        # All nodes must be assigned
        assert set(result.keys()) == set(nodes)

        # Exactly two distinct cluster IDs
        cluster_ids = set(result.values())
        assert len(cluster_ids) == 2, f"Expected 2 clusters, got {cluster_ids}"

    def test_two_community_graph_correct_grouping(self) -> None:
        """All A nodes are in the same cluster; all B nodes in the other."""
        from seam.analysis.clustering import detect_communities

        nodes, edges = self._make_two_community_input()
        result = detect_communities(nodes, edges)

        # a-nodes should share a cluster ID
        a_ids = {result["a1"], result["a2"], result["a3"]}
        assert len(a_ids) == 1, f"a-nodes should be in same cluster, got {a_ids}"

        # b-nodes should share a cluster ID
        b_ids = {result["b1"], result["b2"], result["b3"]}
        assert len(b_ids) == 1, f"b-nodes should be in same cluster, got {b_ids}"

        # The two groups must be in different clusters
        assert a_ids != b_ids, "a-cluster and b-cluster must differ"

    def test_determinism_same_input_same_output(self) -> None:
        """Calling detect_communities twice with the same input returns identical maps."""
        from seam.analysis.clustering import detect_communities

        nodes, edges = self._make_two_community_input()
        result_1 = detect_communities(nodes, edges)
        result_2 = detect_communities(nodes, edges)

        assert result_1 == result_2, (
            f"Non-deterministic output detected:\n  run1={result_1}\n  run2={result_2}"
        )


# ── D2: Disconnected components ───────────────────────────────────────────────


class TestDisconnectedComponents:
    """D2: Two disconnected components → each in its own cluster."""

    def test_two_disconnected_components(self) -> None:
        """Nodes x1/x2 with no edge to y1/y2 should produce two separate clusters."""
        from seam.analysis.clustering import detect_communities

        nodes = ["x1", "x2", "y1", "y2"]
        edges = [
            ("x1", "x2"),  # component 1
            ("y1", "y2"),  # component 2 — no link to x
        ]
        result = detect_communities(nodes, edges)

        assert set(result.keys()) == set(nodes)

        # x-nodes in same cluster
        assert result["x1"] == result["x2"], "x1/x2 must share a cluster"
        # y-nodes in same cluster
        assert result["y1"] == result["y2"], "y1/y2 must share a cluster"
        # x-cluster != y-cluster
        assert result["x1"] != result["y1"], "x-cluster and y-cluster must differ"


# ── D3: Single node ───────────────────────────────────────────────────────────


class TestSingleNode:
    """D3: A single node with no edges should produce one cluster containing itself."""

    def test_single_node_assigned_a_cluster(self) -> None:
        """Single node → exactly one cluster with that node."""
        from seam.analysis.clustering import detect_communities

        result = detect_communities(["lone"], [])
        assert set(result.keys()) == {"lone"}
        # Exactly one cluster ID (its own cluster)
        assert len(set(result.values())) == 1

    def test_singleton_with_connected_nodes(self) -> None:
        """An isolated node alongside connected nodes gets its own cluster."""
        from seam.analysis.clustering import detect_communities

        nodes = ["iso", "c1", "c2"]
        edges = [("c1", "c2")]  # iso is not connected to anything
        result = detect_communities(nodes, edges)

        assert set(result.keys()) == set(nodes)
        # iso has no neighbors — must be in a cluster of its own
        assert result["iso"] != result["c1"]
        assert result["iso"] != result["c2"]


# ── D4: Empty graph ───────────────────────────────────────────────────────────


class TestEmptyGraph:
    """D4: Empty input → empty output map."""

    def test_empty_nodes_and_edges(self) -> None:
        """No nodes, no edges → {}."""
        from seam.analysis.clustering import detect_communities

        result = detect_communities([], [])
        assert result == {}

    def test_nodes_only_no_edges(self) -> None:
        """Nodes with zero edges each get their own singleton cluster."""
        from seam.analysis.clustering import detect_communities

        nodes = ["a", "b", "c"]
        result = detect_communities(nodes, [])

        assert set(result.keys()) == set(nodes)
        # All nodes have no edges → each is a singleton cluster → all IDs distinct
        cluster_ids = list(result.values())
        assert len(cluster_ids) == len(set(cluster_ids)), "Singleton nodes must all have distinct cluster IDs"


# ── D5: Degenerate input — never raises ──────────────────────────────────────


class TestDegenerateInputNeverRaises:
    """D5: Degenerate inputs must not raise; return sensible outputs."""

    def test_self_loop_edge_does_not_raise(self) -> None:
        """An edge from a node to itself must not raise."""
        from seam.analysis.clustering import detect_communities

        try:
            result = detect_communities(["a", "b"], [("a", "a"), ("a", "b")])
            assert "a" in result
            assert "b" in result
        except Exception as exc:
            pytest.fail(f"detect_communities raised on self-loop: {exc}")

    def test_edge_referencing_unknown_node_does_not_raise(self) -> None:
        """An edge referencing a node not in the nodes list must not raise."""
        from seam.analysis.clustering import detect_communities

        try:
            # 'ghost' appears in edge but not in nodes list
            result = detect_communities(["a", "b"], [("a", "ghost")])
            assert "a" in result
            assert "b" in result
        except Exception as exc:
            pytest.fail(f"detect_communities raised on unknown node in edge: {exc}")

    def test_duplicate_edges_do_not_raise(self) -> None:
        """Duplicate edges must be handled without raising."""
        from seam.analysis.clustering import detect_communities

        try:
            result = detect_communities(["a", "b"], [("a", "b"), ("a", "b"), ("b", "a")])
            assert set(result.keys()) == {"a", "b"}
        except Exception as exc:
            pytest.fail(f"detect_communities raised on duplicate edges: {exc}")


# ── D6: Determinism (second assertion on same-input runs) ─────────────────────


class TestDeterminism:
    """D6: Determinism is load-bearing — same graph → byte-identical output."""

    def test_larger_graph_determinism(self) -> None:
        """A larger 3-community graph produces identical results on two runs."""
        from seam.analysis.clustering import detect_communities

        # Three communities of 4 nodes each, sparsely connected
        nodes = [f"c{i}_{j}" for i in range(3) for j in range(4)]
        edges = []
        for i in range(3):
            members = [f"c{i}_{j}" for j in range(4)]
            # Fully connect each community
            for a in range(len(members)):
                for b in range(a + 1, len(members)):
                    edges.append((members[a], members[b]))
        # Weak bridges between communities
        edges.append(("c0_0", "c1_0"))
        edges.append(("c1_0", "c2_0"))

        result_1 = detect_communities(nodes, edges)
        result_2 = detect_communities(nodes, edges)
        assert result_1 == result_2, "detect_communities must be deterministic"

    def test_determinism_independent_of_input_order(self) -> None:
        """Shuffled node order should still yield same cluster grouping."""
        from seam.analysis.clustering import detect_communities

        nodes = ["a1", "a2", "a3", "b1", "b2", "b3"]
        edges = [
            ("a1", "a2"), ("a2", "a3"), ("a1", "a3"),
            ("b1", "b2"), ("b2", "b3"), ("b1", "b3"),
            ("a1", "b1"),
        ]
        # Same nodes/edges but in a different list order
        nodes_shuffled = ["b3", "a2", "b1", "a1", "a3", "b2"]
        edges_shuffled = list(reversed(edges))

        r1 = detect_communities(nodes, edges)
        r2 = detect_communities(nodes_shuffled, edges_shuffled)

        # The cluster-grouping must be the same: a-nodes together, b-nodes together
        # (IDs may differ numerically, but the partition structure must match)
        assert r1["a1"] == r1["a2"] == r1["a3"], "a-nodes always same cluster"
        assert r2["a1"] == r2["a2"] == r2["a3"], "a-nodes always same cluster (shuffled)"
        assert r1["b1"] == r1["b2"] == r1["b3"], "b-nodes always same cluster"
        assert r2["b1"] == r2["b2"] == r2["b3"], "b-nodes always same cluster (shuffled)"
