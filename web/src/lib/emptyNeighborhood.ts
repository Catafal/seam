/**
 * emptyNeighborhood — pure predicate for the A3 empty-state guard.
 *
 * A neighborhood is "empty" when the API returned exactly one node (the symbol
 * itself, always the center) and zero edges.  Zero-node cases are excluded:
 * they indicate the data is still loading or an error occurred, not that the
 * symbol genuinely has no connections.
 */

/**
 * Returns true only when the graph has exactly one node (the center symbol)
 * and no edges — the canonical "no indexed connections" state.
 *
 * Using raw counts rather than arrays so callers can pass `nodes.length` and
 * `edges.length` without constructing temporary objects (keeps this leaf pure
 * and trivially testable).
 */
export function isEmptyNeighborhood(nodeCount: number, edgeCount: number): boolean {
  return nodeCount === 1 && edgeCount === 0;
}
