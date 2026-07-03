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

import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "./client";
import { deriveAreas, type Area } from "../lib/deriveAreas";
import type {
  StatusResponse,
  SearchResultItem,
  NeighborhoodResponse,
  SymbolResponse,
  ClusterItem,
  ImpactResponse,
  TraceResponse,
  ChangesResponse,
  HubSymbol,
  StructureSymbol,
  SchemaResponse,
  ArchitectureResponse,
  SnippetResponse,
  GraphSearchResponse,
} from "./schema-types";
import type {
  SearchResponse,
  ClustersResponse,
  HubsResponse,
  StructureResponse,
  ConstellationResponse,
} from "./schema-types";

/** Impact blast-radius direction (matches the API Literal). */
export type ImpactDirection = "both" | "upstream" | "downstream";
/** Git-diff scope (matches the API Literal). */
export type ChangesScope = "working" | "staged" | "branch";

/** Selectors accepted by GET /api/snippet. */
export interface SnippetSelector {
  uid?: string;
  symbol?: string;
  file?: string;
  line?: number;
  contextLines?: number;
  maxLines?: number;
  maxBytes?: number;
  includeNeighbors?: boolean;
}

/** Typed filters accepted by GET /api/graph/search. */
export interface GraphSearchFilters {
  kind?: string;
  namePattern?: string;
  qualifiedNamePattern?: string;
  filePattern?: string;
  language?: string;
  edgeKind?: string;
  direction?: "incoming" | "outgoing" | "both";
  minDegree?: number;
  maxDegree?: number;
  minInDegree?: number;
  maxInDegree?: number;
  minOutDegree?: number;
  maxOutDegree?: number;
  confidence?: "EXTRACTED" | "INFERRED" | "AMBIGUOUS";
  synthesized?: "any" | "parser" | "synthesized";
  clusterId?: number;
  visibility?: string;
  isExported?: boolean;
  testScope?: "any" | "test" | "source";
  preset?: "dead-code" | "hotspot" | "field-access" | "inheritance" | "isolates";
  sort?: "default" | "in-degree" | "out-degree" | "total-degree" | "name" | "file" | "line";
  limit?: number;
  offset?: number;
  includePreview?: boolean;
  previewLimit?: number;
  regex?: boolean;
}

/** Filters accepted by GET /api/architecture. */
export interface ArchitectureFilters {
  scope?: string;
  sections?: string[];
  limit?: number;
  maxBytes?: number;
}

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

// ── useHubs ─────────────────────────────────────────────────────────────────

/**
 * Fetch the most-connected 'hub' symbols from GET /api/hubs — landing entry points.
 *
 * WHY showTests defaults to false: test helpers accumulate high degree within the
 * test suite but are not meaningful production entry points for most developers.
 * The toggle lets the user opt-in to the full picture (including test graph hubs).
 *
 * @param limit      How many hub symbols to request (default 60).
 * @param showTests  When true, include test-path symbols (default false).
 * @returns data — the flat symbols array (not the wrapper object)
 */
export function useHubs(limit: number = 60, showTests: boolean = false) {
  return useQuery<HubSymbol[]>({
    // Include showTests in the cache key so toggling it triggers a fresh fetch.
    queryKey: ["hubs", limit, showTests],
    queryFn: async () => {
      const resp = await apiFetch<HubsResponse>("/api/hubs", {
        params: { limit, show_tests: showTests },
      });
      return resp.symbols;
    },
  });
}

// ── useStructure ──────────────────────────────────────────────────────────────

/**
 * Fetch the flat symbol+path list from GET /api/structure (treemap source).
 * Disabled when `enabled` is false (only fetch in Overview/structure mode).
 *
 * @returns data — the flat symbols array (the SPA builds the tree from it)
 */
export function useStructure(enabled: boolean = true) {
  return useQuery<StructureSymbol[]>({
    queryKey: ["structure"],
    queryFn: async () => {
      const resp = await apiFetch<StructureResponse>("/api/structure");
      return resp.symbols;
    },
    enabled,
  });
}

// ── useSchema ────────────────────────────────────────────────────────────────

/**
 * Fetch index capability metadata from GET /api/schema.
 * Kept as a full response because callers need freshness, warnings, capabilities,
 * and optionally verbose table metadata.
 */
export function useSchema(verbose: boolean = false, enabled: boolean = true) {
  return useQuery<SchemaResponse>({
    queryKey: ["schema", verbose],
    queryFn: () =>
      apiFetch<SchemaResponse>("/api/schema", { params: { verbose } }),
    enabled,
  });
}

// ── useArchitecture ─────────────────────────────────────────────────────────

/**
 * Fetch a bounded repository architecture briefing from GET /api/architecture.
 * Kept as a full response because sections, warnings, truncation, and next calls
 * are all part of the overview contract.
 */
export function useArchitecture(
  filters: ArchitectureFilters = {},
  enabled: boolean = true,
) {
  return useQuery<ArchitectureResponse>({
    queryKey: ["architecture", filters],
    queryFn: () =>
      apiFetch<ArchitectureResponse>("/api/architecture", {
        params: {
          scope: filters.scope,
          section: filters.sections?.join(","),
          limit: filters.limit,
          max_bytes: filters.maxBytes,
        },
      }),
    enabled,
  });
}

// ── useSnippet ──────────────────────────────────────────────────────────────

/**
 * Fetch bounded exact source from GET /api/snippet.
 * Disabled until one complete selector is available.
 */
export function useSnippet(selector: SnippetSelector, enabled: boolean = true) {
  const hasUid = selector.uid !== undefined && selector.uid.trim().length > 0;
  const hasSymbol = selector.symbol !== undefined && selector.symbol.trim().length > 0;
  const hasLocation =
    selector.file !== undefined &&
    selector.file.trim().length > 0 &&
    selector.line !== undefined;

  return useQuery<SnippetResponse>({
    queryKey: ["snippet", selector],
    queryFn: () =>
      apiFetch<SnippetResponse>("/api/snippet", {
        params: {
          uid: selector.uid,
          symbol: selector.symbol,
          file: selector.file,
          line: selector.line,
          context_lines: selector.contextLines,
          max_lines: selector.maxLines,
          max_bytes: selector.maxBytes,
          include_neighbors: selector.includeNeighbors,
        },
      }),
    enabled: enabled && (hasUid || hasSymbol || hasLocation),
  });
}

// ── useGraphSearch ──────────────────────────────────────────────────────────

/**
 * Fetch typed structural graph-search results from GET /api/graph/search.
 * Kept as a full response because pagination, warnings, and normalized query
 * metadata are part of the graph-search contract.
 */
export function useGraphSearch(filters: GraphSearchFilters = {}, enabled: boolean = true) {
  return useQuery<GraphSearchResponse>({
    queryKey: ["graph-search", filters],
    queryFn: () =>
      apiFetch<GraphSearchResponse>("/api/graph/search", {
        params: {
          kind: filters.kind,
          name_pattern: filters.namePattern,
          qualified_name_pattern: filters.qualifiedNamePattern,
          file_pattern: filters.filePattern,
          language: filters.language,
          edge_kind: filters.edgeKind,
          direction: filters.direction,
          min_degree: filters.minDegree,
          max_degree: filters.maxDegree,
          min_in_degree: filters.minInDegree,
          max_in_degree: filters.maxInDegree,
          min_out_degree: filters.minOutDegree,
          max_out_degree: filters.maxOutDegree,
          confidence: filters.confidence,
          synthesized: filters.synthesized,
          cluster_id: filters.clusterId,
          visibility: filters.visibility,
          is_exported: filters.isExported,
          test_scope: filters.testScope,
          preset: filters.preset,
          sort: filters.sort,
          limit: filters.limit,
          offset: filters.offset,
          include_preview: filters.includePreview,
          preview_limit: filters.previewLimit,
          regex: filters.regex,
        },
      }),
    enabled,
  });
}

// ── useConstellation ─────────────────────────────────────────────────────────

/**
 * Fetch the whole-repo cluster topology from GET /api/constellation.
 * Always enabled — used by the 2D cluster graph (C2).
 *
 * @returns data — the full ConstellationResponse { clusters, links }
 */
export function useConstellation() {
  return useQuery<ConstellationResponse>({
    queryKey: ["constellation"],
    queryFn: () => apiFetch<ConstellationResponse>("/api/constellation"),
  });
}

// ── useAreas ─────────────────────────────────────────────────────────────────

/** Result shape returned by useAreas. */
export interface UseAreasResult {
  /** Derived functional areas (folder-based, sorted by symbol count desc). */
  areas: Area[];
  /** True while either /api/structure or /api/hubs is still fetching. */
  isLoading: boolean;
}

/**
 * Shared hook that composes useStructure + useHubs + deriveAreas into one
 * fetch-and-derive result so the landing and the Overview both derive areas
 * from exactly one place.
 *
 * WHY a shared hook (not inline derivation in each consumer): this guarantees
 * landing and Overview cannot drift apart — the "area" concept is defined once.
 * deriveAreas is unchanged (pure, unit-tested); this hook is the single wiring
 * point. See Phase B PRD §"One areas concept".
 *
 * @param opts.includeTests  when false (default), test directories are excluded.
 */
export function useAreas(opts: { includeTests: boolean }): UseAreasResult {
  // Fetch structure always enabled — the Overview and landing both need it.
  const { data: symbols, isLoading: structureLoading } = useStructure(true);
  // 60 hubs gives good coverage for keySymbol hints across all areas.
  // We do NOT filter hubs by showTests here: deriveAreas skips hub entries
  // that fall in filtered-out (test) areas automatically.
  const { data: hubs, isLoading: hubsLoading } = useHubs(60);

  const areas = useMemo(
    () => deriveAreas(symbols ?? [], hubs ?? [], { includeTests: opts.includeTests }),
    [symbols, hubs, opts.includeTests],
  );

  return {
    areas,
    isLoading: structureLoading || hubsLoading,
  };
}
