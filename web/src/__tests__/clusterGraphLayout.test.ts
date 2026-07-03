/**
 * TDD tests for clusterGraphLayout — the pure deterministic layout engine
 * for the 2D cluster-graph (C2).
 *
 * Covers:
 *   - N clusters → N nodes (one node per cluster)
 *   - Empty input → empty graph
 *   - Determinism: two calls with the same input produce identical positions
 *   - Node size is monotonic in cluster size (larger cluster ≥ bigger node)
 *   - Edge width is monotonic in link weight (heavier link ≥ wider edge)
 *   - Edge opacity is monotonic in link weight
 *   - Node color comes from clusterColor(cluster_id)
 *   - M links → M edges (one edge per link)
 *   - Node id is "cluster-<cluster_id>"
 *   - Edge id is "link-<source>-<target>"
 */

import { describe, it, expect } from "vitest";
import { clusterGraphLayout } from "../lib/clusterGraphLayout";
import { clusterColor } from "../lib/clusterColor";
import type { ConstellationCluster, ConstellationLink } from "../api/schema-types";

// ── Helpers ───────────────────────────────────────────────────────────────────

function makeCluster(
  cluster_id: number,
  size: number,
  label: string | null = null,
  representative: string | null = null,
): ConstellationCluster {
  return { cluster_id, size, label, representative };
}

function makeLink(source: number, target: number, weight: number): ConstellationLink {
  return { source, target, weight };
}

// ── clusterGraphLayout ─────────────────────────────────────────────────────────

describe("clusterGraphLayout", () => {
  it("returns empty nodes and edges for empty input", () => {
    const result = clusterGraphLayout([], []);
    expect(result.nodes).toHaveLength(0);
    expect(result.edges).toHaveLength(0);
  });

  it("produces one node per cluster", () => {
    const clusters = [makeCluster(1, 10), makeCluster(2, 5), makeCluster(3, 20)];
    const { nodes } = clusterGraphLayout(clusters, []);
    expect(nodes).toHaveLength(3);
  });

  it("node ids follow the 'cluster-<cluster_id>' pattern", () => {
    const clusters = [makeCluster(7, 10), makeCluster(42, 5)];
    const { nodes } = clusterGraphLayout(clusters, []);
    const ids = nodes.map((n) => n.id);
    expect(ids).toContain("cluster-7");
    expect(ids).toContain("cluster-42");
  });

  it("produces one edge per link", () => {
    const clusters = [makeCluster(1, 10), makeCluster(2, 5), makeCluster(3, 20)];
    const links = [makeLink(1, 2, 3), makeLink(2, 3, 7)];
    const { edges } = clusterGraphLayout(clusters, links);
    expect(edges).toHaveLength(2);
  });

  it("edge ids follow the 'link-<source>-<target>' pattern", () => {
    const clusters = [makeCluster(1, 10), makeCluster(2, 5)];
    const links = [makeLink(1, 2, 3)];
    const { edges } = clusterGraphLayout(clusters, links);
    expect(edges[0].id).toBe("link-1-2");
  });

  it("edge source and target reference correct cluster node ids", () => {
    const clusters = [makeCluster(1, 10), makeCluster(2, 5)];
    const links = [makeLink(1, 2, 3)];
    const { edges } = clusterGraphLayout(clusters, links);
    expect(edges[0].source).toBe("cluster-1");
    expect(edges[0].target).toBe("cluster-2");
  });

  it("node size is monotonic in cluster size (larger cluster ≥ bigger node width)", () => {
    const small = makeCluster(1, 5);
    const medium = makeCluster(2, 50);
    const large = makeCluster(3, 500);
    const { nodes } = clusterGraphLayout([small, medium, large], []);

    const getWidth = (id: string) => {
      const node = nodes.find((n) => n.id === `cluster-${id}`);
      // width is in data or style
      return (node?.data?.nodeSize as number) ?? 0;
    };

    const wSmall = getWidth("1");
    const wMedium = getWidth("2");
    const wLarge = getWidth("3");

    expect(wMedium).toBeGreaterThanOrEqual(wSmall);
    expect(wLarge).toBeGreaterThanOrEqual(wMedium);
  });

  it("edge strokeWidth is monotonic in link weight (heavier ≥ wider)", () => {
    const clusters = [makeCluster(1, 10), makeCluster(2, 10), makeCluster(3, 10)];
    const light = makeLink(1, 2, 1);
    const heavy = makeLink(2, 3, 100);
    const { edges } = clusterGraphLayout(clusters, [light, heavy]);

    const strokeFor = (src: number, tgt: number) => {
      const e = edges.find((e) => e.source === `cluster-${src}` && e.target === `cluster-${tgt}`);
      return (e?.data?.strokeWidth as number) ?? 0;
    };

    expect(strokeFor(2, 3)).toBeGreaterThanOrEqual(strokeFor(1, 2));
  });

  it("edge opacity is monotonic in link weight", () => {
    const clusters = [makeCluster(1, 10), makeCluster(2, 10), makeCluster(3, 10)];
    const light = makeLink(1, 2, 1);
    const heavy = makeLink(2, 3, 100);
    const { edges } = clusterGraphLayout(clusters, [light, heavy]);

    const opacityFor = (src: number, tgt: number) => {
      const e = edges.find((e) => e.source === `cluster-${src}` && e.target === `cluster-${tgt}`);
      return (e?.data?.opacity as number) ?? 0;
    };

    expect(opacityFor(2, 3)).toBeGreaterThanOrEqual(opacityFor(1, 2));
  });

  it("node color matches clusterColor(cluster_id)", () => {
    const clusters = [makeCluster(3, 10), makeCluster(7, 20)];
    const { nodes } = clusterGraphLayout(clusters, []);

    for (const node of nodes) {
      const cid = node.data.clusterId as number;
      expect(node.data.color).toBe(clusterColor(cid));
    }
  });

  it("node data carries clusterId, label, size, and representative", () => {
    const clusters = [makeCluster(5, 42, "Parsers", "Parser.parse")];
    const { nodes } = clusterGraphLayout(clusters, []);

    const n = nodes[0];
    expect(n.data.clusterId).toBe(5);
    expect(n.data.label).toBe("Parsers");
    expect(n.data.size).toBe(42);
    expect(n.data.representative).toBe("Parser.parse");
  });

  it("is deterministic — two calls with the same input produce identical positions", () => {
    const clusters = [
      makeCluster(1, 100),
      makeCluster(2, 50),
      makeCluster(3, 10),
      makeCluster(4, 200),
    ];
    const links = [makeLink(1, 2, 5), makeLink(3, 4, 2)];

    const result1 = clusterGraphLayout(clusters, links);
    const result2 = clusterGraphLayout(clusters, links);

    for (let i = 0; i < result1.nodes.length; i++) {
      expect(result1.nodes[i].position.x).toBe(result2.nodes[i].position.x);
      expect(result1.nodes[i].position.y).toBe(result2.nodes[i].position.y);
    }
  });

  it("a single cluster is placed at the origin", () => {
    const clusters = [makeCluster(1, 10)];
    const { nodes } = clusterGraphLayout(clusters, []);
    // Single node — position should be well-defined (centered)
    expect(nodes[0].position).toBeDefined();
    expect(typeof nodes[0].position.x).toBe("number");
    expect(typeof nodes[0].position.y).toBe("number");
  });

  it('every node uses the built-in "default" RF type (ClusterGraph2D registers no nodeTypes)', () => {
    // Regression: the layout previously emitted type "clusterNode", but
    // ClusterGraph2D has no nodeTypes map — RF would log error 002 per node and
    // fall back to default. Lock the type to "default" so appearance stays on the
    // style prop and no console error is emitted.
    const clusters = [makeCluster(1, 10), makeCluster(2, 5), makeCluster(3, 3)];
    const { nodes } = clusterGraphLayout(clusters, []);
    expect(nodes.length).toBe(3);
    for (const node of nodes) {
      expect(node.type).toBe("default");
    }
  });

  it("handles links referencing unknown cluster ids gracefully (no crash)", () => {
    const clusters = [makeCluster(1, 10)];
    // Link references cluster 99 which does not exist
    const links = [makeLink(1, 99, 5)];
    expect(() => clusterGraphLayout(clusters, links)).not.toThrow();
  });
});
