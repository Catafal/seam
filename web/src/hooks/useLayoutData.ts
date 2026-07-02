/**
 * React-query hook for fetching the 3D constellation layout from the backend.
 * GET /api/graph/layout?max_nodes=N → LayoutData
 *
 * WHY 60 s stale time?
 *   The layout response is typically 200 kB–2 MB of JSON (2 k nodes). Re-fetching
 *   on every render or tab-switch would stall the browser on large repos. 60 s
 *   matches the typical "I edited a few files and ran seam sync" cycle; the
 *   react-query refetchOnWindowFocus default ensures a re-fetch on the next tab
 *   activation after the stale window expires. The server-side cache (keyed on
 *   MAX(indexed_at)) ensures the response is always index-consistent regardless
 *   of this client-side TTL.
 *
 * WHY a lazy import boundary here (ConstellationTab → dynamic import)?
 *   R3F (@react-three/fiber, @react-three/drei, @react-three/postprocessing) adds
 *   ~800 kB to the JS bundle. The Constellation tab is one of several Explorer tabs
 *   and users may never open it. ConstellationTab (and therefore this hook) is
 *   loaded via React.lazy() / Suspense in App.tsx, splitting R3F into a separate
 *   chunk that is only fetched when the tab is first activated. Without this split
 *   the initial Explorer page load would always pay the R3F parse cost.
 *
 * Error/loading states are handled by the caller (ConstellationTab).
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
