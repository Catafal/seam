/**
 * Graph filter state — combined node-kind + edge-kind + confidence filter
 * for the 2D GraphCanvas.
 *
 * WHY a separate module from edgeFilter.ts: the original module is edge-only
 * and is shared with useGraphOverlays. This module adds the node-kind axis and
 * localStorage persistence without changing the EdgeFilterState contract.
 *
 * Persistence strategy: store explicitly DISABLED kind sets, not enabled sets.
 * WHY: new kinds added to the vocabulary default to ENABLED automatically because
 * they won't appear in the persisted disabled set. Stale disabled kinds (removed
 * from the vocabulary) are silently ignored during merge. No migration needed.
 *
 * Session-global semantics: GraphCanvas initializes from loadGraphFilter() so
 * the preference survives page reload. Because filter state is never reset in
 * the center-change effect, it also survives symbol navigation within a session.
 */

import { ALL_EDGE_KINDS, ALL_CONFIDENCES, type EdgeFilterState } from "./edgeFilter";

// ── Node kind vocabulary ───────────────────────────────────────────────────────

/** All node kinds Seam indexes — stable display order. */
export const ALL_NODE_KINDS = [
  "function",
  "class",
  "method",
  "interface",
  "type",
  "field",
] as const;

export type NodeKind = (typeof ALL_NODE_KINDS)[number];

// ── State type ─────────────────────────────────────────────────────────────────

/** Combined filter state: node kinds + edge kinds + confidence tiers. */
export interface GraphFilterState extends EdgeFilterState {
  /** Which node kinds are currently visible on the canvas. */
  nodeKinds: Set<string>;
}

// ── Default state ──────────────────────────────────────────────────────────────

/** Create a fresh default: all node kinds + edge kinds + confidence tiers enabled. */
export function defaultGraphFilter(): GraphFilterState {
  return {
    nodeKinds: new Set(ALL_NODE_KINDS),
    kinds: new Set(ALL_EDGE_KINDS),
    confidences: new Set(ALL_CONFIDENCES),
  };
}

// ── Immutable updates ──────────────────────────────────────────────────────────

/**
 * Toggle a single node kind on/off.
 * Returns a new state — original is never mutated.
 */
export function toggleNodeKind(state: GraphFilterState, kind: string): GraphFilterState {
  const next = new Set(state.nodeKinds);
  if (next.has(kind)) {
    next.delete(kind);
  } else {
    next.add(kind);
  }
  return { ...state, nodeKinds: next };
}

/** Enable all node kinds. Returns a new state. */
export function allNodeKinds(state: GraphFilterState): GraphFilterState {
  return { ...state, nodeKinds: new Set(ALL_NODE_KINDS) };
}

/** Disable all node kinds (canvas shows no symbol nodes). Returns a new state. */
export function noneNodeKinds(state: GraphFilterState): GraphFilterState {
  return { ...state, nodeKinds: new Set() };
}

// ── Serialized form ────────────────────────────────────────────────────────────

/** Shape stored in localStorage — only explicitly DISABLED kinds are persisted. */
interface PersistedFilter {
  /** Node kind names that are explicitly hidden. */
  disabledNodeKinds: string[];
  /** Edge kind names that are explicitly hidden. */
  disabledEdgeKinds: string[];
  /** Confidence tier names that are explicitly hidden. */
  disabledConfidences: string[];
}

const LS_FILTER_KEY = "seam-graph-filter";

// ── localStorage save/load ─────────────────────────────────────────────────────

/**
 * Persist the current filter state to localStorage.
 * Silently ignores errors (quota exceeded, SSR environments).
 */
export function saveGraphFilter(state: GraphFilterState): void {
  try {
    const persisted: PersistedFilter = {
      disabledNodeKinds: ALL_NODE_KINDS.filter((k) => !state.nodeKinds.has(k)),
      disabledEdgeKinds: [...ALL_EDGE_KINDS].filter((k) => !state.kinds.has(k)),
      disabledConfidences: [...ALL_CONFIDENCES].filter((c) => !state.confidences.has(c)),
    };
    localStorage.setItem(LS_FILTER_KEY, JSON.stringify(persisted));
  } catch {
    /* ignore quota / SSR errors */
  }
}

/**
 * Load persisted filter state from localStorage and merge with current defaults.
 *
 * Returns defaultGraphFilter() if nothing is persisted or on parse error.
 * Unknown / stale kind names in the persisted data are silently ignored.
 */
export function loadGraphFilter(): GraphFilterState {
  const defaults = defaultGraphFilter();
  try {
    const raw = localStorage.getItem(LS_FILTER_KEY);
    if (!raw) return defaults;
    const persisted = JSON.parse(raw) as Partial<PersistedFilter>;
    return mergeWithDefaults(persisted, defaults);
  } catch {
    return defaults;
  }
}

/**
 * Merge a (possibly stale) persisted disabled-kind record with current defaults.
 *
 * Rules:
 * - A kind that appears in the disabled set → disabled in result.
 * - A kind in the current vocabulary that is NOT in the disabled set → enabled.
 * - Stale entries in the disabled set (no longer in vocabulary) → silently ignored.
 *
 * Pure — no side effects, exported for unit testing.
 */
export function mergeWithDefaults(
  persisted: Partial<PersistedFilter>,
  defaults: GraphFilterState,
): GraphFilterState {
  const disabledNodeKinds = new Set(persisted.disabledNodeKinds ?? []);
  const disabledEdgeKinds = new Set(persisted.disabledEdgeKinds ?? []);
  const disabledConfidences = new Set(persisted.disabledConfidences ?? []);

  // Start from the defaults vocabulary (which is always current), then remove
  // any kind that was explicitly disabled. Stale entries in the disabled set
  // won't match anything in defaults and are naturally ignored.
  const nodeKinds = new Set(
    [...defaults.nodeKinds].filter((k) => !disabledNodeKinds.has(k)),
  );
  const kinds = new Set(
    [...defaults.kinds].filter((k) => !disabledEdgeKinds.has(k)),
  );
  const confidences = new Set(
    [...defaults.confidences].filter((c) => !disabledConfidences.has(c)),
  );

  return { nodeKinds, kinds, confidences };
}
