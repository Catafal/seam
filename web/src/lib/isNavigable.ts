/**
 * isNavigable — pure predicate deciding whether a SymbolNode can be usefully
 * expanded / re-centered on double-click (#286).
 *
 * WHY this predicate exists:
 *   The graph indexes both explicitly-declared symbols AND bare edge-target
 *   references (helper names that appear only as call targets, never as top-level
 *   declarations). A bare-target node has definition_count === 0 because no
 *   symbol row in the DB backs it. Navigating to it produces either the
 *   EmptyNeighborhoodState or a stale/blank neighborhood — confusing UX.
 *
 *   For private/restricted visibility nodes with zero definitions the situation
 *   is worse: the node exists only as a side-reference and clicking it to "expand"
 *   gives the user nothing useful. We gate the double-click to be a no-op so the
 *   user is not invited to navigate to a dead end.
 *
 *   Single-click (detail panel) is ALWAYS allowed — even non-navigable nodes
 *   may carry useful metadata (signature, kind, cluster) for inspection.
 *
 * Decision rules (conservative — when uncertain, return true so the existing
 * EmptyNeighborhoodState handles the empty result gracefully):
 *
 *   1. Center nodes are always navigable (they ARE the current neighborhood).
 *   2. Public nodes are navigable even with zero definitions (stale index may
 *      explain the missing definition; EmptyNeighborhoodState handles it gracefully).
 *   3. Unknown-visibility (null) AND zero definitions → non-navigable. A null-
 *      visibility node with no definition row is a bare edge-target reference that
 *      appeared only as a call-site name. Navigating it produces nothing useful.
 *   4. Restricted-visibility (private/protected/crate) AND zero definitions →
 *      non-navigable for the same reason as rule 3.
 *   5. Any node WITH at least one definition (definition_count >= 1) is navigable
 *      regardless of visibility — private methods still have callers worth seeing.
 */

import type { SymbolNodeData } from "../components/SymbolNode";

/** Visibility values that indicate restricted access. */
const RESTRICTED_VISIBILITIES = new Set(["private", "protected", "crate"]);

/**
 * Returns true when the node supports double-click expand / re-center.
 *
 * Non-navigable when: definition_count === 0 AND visibility is NOT explicitly
 * public — covers both bare edge-target references (null visibility) and
 * private/restricted helpers with no indexed definition body.
 */
export function isNavigable(data: SymbolNodeData): boolean {
  // Rule 1: center nodes are always navigable (they ARE the current neighborhood).
  if (data.isCenter) return true;

  // Rule 5: any node with an indexed definition is navigable.
  if (data.definition_count > 0) return true;

  // definition_count === 0 from here on.
  // Rule 2: explicitly public nodes are navigable even without a definition row
  // (likely a stale index — EmptyNeighborhoodState communicates that cleanly).
  if (data.visibility === "public") return true;

  // Rule 3 + 4: null or restricted visibility + no definition → bare reference.
  // Either unknown (null) or locked (private/protected/crate); in both cases
  // navigating produces nothing useful.
  const isRestricted =
    data.visibility != null && RESTRICTED_VISIBILITIES.has(data.visibility);
  if (isRestricted || data.visibility == null) return false;

  // Fallback: be conservative — allow navigation.
  return true;
}
