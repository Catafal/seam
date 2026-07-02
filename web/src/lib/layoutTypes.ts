/**
 * TypeScript types for the 3D constellation layout API response.
 *
 * MI1: Use Layout* names (LayoutNode/LayoutEdge) to avoid colliding with the
 * existing 2D graph_api GraphNode/GraphEdge (different shapes, different API).
 */

export type LayoutNode = {
  id: number;
  x: number;
  y: number;
  z: number;
  /** symbol kind: "function" | "class" | "method" | "interface" | "type" | "field" */
  label: string;
  name: string;
  file_path: string | null;
  size: number;
  color: string;
};

export type LayoutEdge = {
  source: number;
  target: number;
  /** edge kind: "call" | "import" | "extends" | "implements" | "instantiates" | "holds" | "reads" | "writes" | "uses" */
  type: string;
};

export type ClusterSummary = {
  cluster_id: number;
  label: string | null;
  centroid: [number, number, number];
  radius: number;
  color: string;
};

export type LayoutData = {
  nodes: LayoutNode[];
  edges: LayoutEdge[];
  clusters: ClusterSummary[];
  total_nodes: number;
};
