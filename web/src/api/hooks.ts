/**
 * TanStack Query hooks for the Seam Explorer API.
 *
 * Design decisions:
 * - Each hook is a thin wrapper over apiFetch with a stable queryKey.
 * - `enabled` guards disable fetching when required params are missing/empty
 *   (avoids spurious "empty query" API calls).
 * - staleTime: hooks rely on the QueryClient-level staleTime (60s, set in main.tsx).
 *   No per-hook override — the global default is appropriate for a local explorer
 *   where the index rarely changes while browsing.
 * - Return shapes: hooks unwrap the response envelope where sensible (e.g.
 *   useSearch returns results[], useClusters returns clusters[]) so callers
 *   don't have to destructure a wrapper object every time.
 * - useNeighborhood and useSymbol return the full response objects because
 *   callers need multiple fields (nodes + edges, definitions + callers + etc.).
 */

import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "./client";
import type {
  StatusResponse,
  SearchResultItem,
  NeighborhoodResponse,
  SymbolResponse,
  ClusterItem,
} from "./schema-types";
import type { SearchResponse, ClustersResponse } from "./schema-types";

// ── useStatus ─────────────────────────────────────────────────────────────────

/**
 * Fetch index statistics from GET /api/status.
 * Always enabled — useful in the header to show live index metadata.
 */
export function useStatus() {
  return useQuery<StatusResponse>({
    queryKey: ["status"],
    queryFn: () => apiFetch<StatusResponse>("/api/status"),
  });
}

// ── useSearch ─────────────────────────────────────────────────────────────────

/**
 * Fetch search results from GET /api/search?q=<query>.
 * Disabled when q is empty to avoid unnecessary API calls.
 *
 * @returns data — the flat results array (not the wrapper object)
 */
export function useSearch(q: string, limit: number = 20) {
  return useQuery<SearchResultItem[]>({
    queryKey: ["search", q, limit],
    queryFn: async () => {
      const resp = await apiFetch<SearchResponse>("/api/search", {
        params: { q, limit },
      });
      return resp.results;
    },
    // Only fetch when the query is non-empty
    enabled: q.trim().length > 0,
  });
}

// ── useNeighborhood ───────────────────────────────────────────────────────────

/**
 * Fetch depth-1 neighborhood graph from GET /api/graph/neighborhood.
 * Disabled when symbol is empty (no center node to expand).
 *
 * @param symbol     Symbol name to center the graph on
 * @param direction  "both" | "callers" | "callees" (default "both")
 * @returns data — the full NeighborhoodResponse { center, nodes, edges }
 */
export function useNeighborhood(
  symbol: string,
  direction: "both" | "callers" | "callees" = "both",
) {
  return useQuery<NeighborhoodResponse>({
    queryKey: ["neighborhood", symbol, direction],
    queryFn: () =>
      apiFetch<NeighborhoodResponse>("/api/graph/neighborhood", {
        params: { symbol, direction },
      }),
    // Only fetch when a center symbol is set
    enabled: symbol.trim().length > 0,
  });
}

// ── useSymbol ─────────────────────────────────────────────────────────────────

/**
 * Fetch full symbol detail from GET /api/symbol/{name}.
 * Disabled when name is null (no symbol selected).
 *
 * @param name  Symbol name, or null when no symbol is selected
 * @returns data — the full SymbolResponse
 */
export function useSymbol(name: string | null) {
  return useQuery<SymbolResponse>({
    queryKey: ["symbol", name],
    queryFn: () => apiFetch<SymbolResponse>(`/api/symbol/${encodeURIComponent(name!)}`),
    // Only fetch when a symbol name is provided
    enabled: name !== null && name.trim().length > 0,
  });
}

// ── useClusters ───────────────────────────────────────────────────────────────

/**
 * Fetch all clusters from GET /api/clusters.
 * Always enabled — used for the landing entry-point list and legend.
 *
 * @returns data — the flat clusters array (not the wrapper object)
 */
export function useClusters() {
  return useQuery<ClusterItem[]>({
    queryKey: ["clusters"],
    queryFn: async () => {
      const resp = await apiFetch<ClustersResponse>("/api/clusters");
      return resp.clusters;
    },
  });
}
