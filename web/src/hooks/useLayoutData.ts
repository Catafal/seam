/**
 * React-query hook for fetching the 3D constellation layout from the backend.
 * GET /api/graph/layout?max_nodes=N → LayoutData
 *
 * Stale time: 60s (layout is expensive to compute and changes only when the
 * index changes). Error/loading states are handled by the caller component.
 */
import { useQuery } from "@tanstack/react-query";
import type { LayoutData } from "../lib/layoutTypes";

/** Default node cap matches SEAM_LAYOUT_MAX_NODES (backend default). */
export const GRAPH_RENDER_NODE_LIMIT = 2000;

async function fetchLayout(maxNodes: number): Promise<LayoutData> {
  const res = await fetch(`/api/graph/layout?max_nodes=${maxNodes}`);
  if (!res.ok) throw new Error(`layout ${res.status}`);
  return res.json() as Promise<LayoutData>;
}

/**
 * Fetch and cache the 3D layout data.
 *
 * @param maxNodes - max rendered nodes (default 2000); must match a valid
 *   backend value ≤ SEAM_LAYOUT_MAX_SAFE_NODES (3000).
 */
export function useLayoutData(maxNodes: number = GRAPH_RENDER_NODE_LIMIT) {
  return useQuery({
    queryKey: ["layout", maxNodes],
    queryFn: () => fetchLayout(maxNodes),
    staleTime: 60_000, // layout is stable for at least 1 minute
  });
}
