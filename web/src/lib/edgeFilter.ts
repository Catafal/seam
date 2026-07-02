/**
 * Client-side edge visibility filtering for the GraphCanvas FilterBar.
 *
 * The filter is purely visual — it never refetches. Toggling a kind or confidence
 * tier off hides those edges (and is applied as a React Flow `hidden` flag), so
 * the user can declutter a dense neighborhood without losing the underlying graph.
 *
 * WHY a pure predicate in its own module: it's trivially unit-testable and keeps
 * the toggle logic out of the canvas component.
 */

/** What the FilterBar tracks: which edge kinds + confidence tiers are visible. */
export interface EdgeFilterState {
  /** Edge kinds currently shown (e.g. {"call","import"}). */
  kinds: Set<string>;
  /** Confidence tiers currently shown (e.g. {"EXTRACTED","AMBIGUOUS","INFERRED"}). */
  confidences: Set<string>;
}

/** All edge kinds and confidence tiers Seam can emit (the default "all on" set). */
export const ALL_EDGE_KINDS = [
  "call",
  "import",
  "extends",
  "implements",
  "instantiates",
  "holds",
  "reads",
  "writes",
  "uses",
  "http_calls",
  "reads_config",
  "configures",
  "raises",
  "catches",
  "tests",
] as const;
export const ALL_CONFIDENCES = ["EXTRACTED", "AMBIGUOUS", "INFERRED"] as const;

/** Default filter state: everything visible. */
export function defaultEdgeFilter(): EdgeFilterState {
  return {
    kinds: new Set(ALL_EDGE_KINDS),
    confidences: new Set(ALL_CONFIDENCES),
  };
}

/**
 * Is an edge visible under the current filter?
 * An edge is shown only when BOTH its kind and confidence are enabled.
 */
export function isEdgeVisible(
  edge: { kind: string; confidence: string },
  filter: EdgeFilterState,
): boolean {
  return filter.kinds.has(edge.kind) && filter.confidences.has(edge.confidence);
}

/** Immutably toggle a value in a Set field, returning a new EdgeFilterState. */
export function toggleFilterValue(
  filter: EdgeFilterState,
  field: "kinds" | "confidences",
  value: string,
): EdgeFilterState {
  const next = new Set(filter[field]);
  if (next.has(value)) {
    next.delete(value);
  } else {
    next.add(value);
  }
  return { ...filter, [field]: next };
}
