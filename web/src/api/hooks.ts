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
  ImpactResponse,
  TraceResponse,
  ChangesResponse,
  ConstellationResponse,
} from "./schema-types";
import type { SearchResponse, ClustersResponse } from "./schema-types";

/** Impact blast-radius direction (matches the API Literal). */
export type ImpactDirection = "both" | "upstream" | "downstream";
/** Git-diff scope (matches the API Literal). */
export type ChangesScope = "working" | "staged" | "branch";

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

// ── useImpact ───────────────────────────────────────────────────────────────────

/**
 * Fetch blast-radius analysis from GET /api/impact.
 * Disabled when `symbol` is empty OR `enabled` is false (overlay is opt-in:
 * we only fetch when the user explicitly asks for the impact view).
 *
 * @param symbol     Target symbol name
 * @param direction  "both" | "upstream" | "downstream" (default "both")
 * @param enabled    Caller gate — fetch only when the overlay is requested
 * @returns data — the full ImpactResponse
 */
export function useImpact(
  symbol: string | null,
  direction: ImpactDirection = "both",
  enabled: boolean = true,
) {
  return useQuery<ImpactResponse>({
    queryKey: ["impact", symbol, direction],
    queryFn: () =>
      apiFetch<ImpactResponse>("/api/impact", {
        params: { symbol: symbol!, direction },
      }),
    enabled: enabled && symbol !== null && symbol.trim().length > 0,
  });
}

// ── useTrace ────────────────────────────────────────────────────────────────────

/**
 * Fetch the shortest path between two symbols from GET /api/trace.
 * Disabled until BOTH source and target are set (a trace needs two endpoints).
 *
 * @returns data — the full TraceResponse { found, source, target, paths }
 */
export function useTrace(source: string | null, target: string | null) {
  return useQuery<TraceResponse>({
    queryKey: ["trace", source, target],
    queryFn: () =>
      apiFetch<TraceResponse>("/api/trace", {
        params: { source: source!, target: target! },
      }),
    enabled:
      source !== null &&
      target !== null &&
      source.trim().length > 0 &&
      target.trim().length > 0,
  });
}

// ── useChanges ──────────────────────────────────────────────────────────────────

/**
 * Fetch git-diff changed symbols + risk from GET /api/changes.
 * Disabled when `enabled` is false (the drawer is opt-in to avoid a git call
 * on every page load).
 *
 * @param scope    "working" | "staged" | "branch" (default "working")
 * @param enabled  Caller gate — fetch only when the drawer is open
 * @returns data — the full ChangesResponse
 */
export function useChanges(scope: ChangesScope = "working", enabled: boolean = true) {
  return useQuery<ChangesResponse>({
    queryKey: ["changes", scope],
    queryFn: () =>
      apiFetch<ChangesResponse>("/api/changes", { params: { scope } }),
    enabled,
    // Changes reflect the live working tree — don't serve a stale cache.
    staleTime: 0,
    // A non-git repo returns 400; no point retrying that.
    retry: false,
  });
}

// ── useConstellation ──────────────────────────────────────────────────────────

/**
 * Fetch the whole-repo cluster overview from GET /api/constellation.
 * Disabled when `enabled` is false (only fetch in overview mode).
 *
 * @returns data — the full ConstellationResponse { clusters, links }
 */
export function useConstellation(enabled: boolean = true) {
  return useQuery<ConstellationResponse>({
    queryKey: ["constellation"],
    queryFn: () => apiFetch<ConstellationResponse>("/api/constellation"),
    enabled,
  });
}
