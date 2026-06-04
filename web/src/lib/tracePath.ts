/**
 * Trace-overlay helper: turn a TraceResponse into the set of nodes + edges to
 * highlight for the shortest path.
 *
 * The canvas bolds the path edges + lights the path nodes, and dims everything
 * else, so the route from source to target stands out. We highlight ONLY the
 * shortest path (paths[0]); deeper paths would clutter the view.
 *
 * Edge keys are `"<source>-><target>"` so the canvas can match them against RF
 * edges (which carry source/target by name).
 */

import type { TraceResponse } from "../api/schema-types";

export interface TraceHighlight {
  /** Symbol names on the highlighted path. */
  nodeNames: Set<string>;
  /** Edge keys ("from->to") on the highlighted path. */
  edgeKeys: Set<string>;
  /** True when there is a path to highlight (drives dim-everything-else mode). */
  active: boolean;
}

/** Build the edge key the canvas uses to match a directed edge. */
export function edgeKey(source: string, target: string): string {
  return `${source}->${target}`;
}

/**
 * Extract the highlight sets from the shortest path of a trace result.
 * Returns inactive/empty sets for undefined / not-found / empty results.
 */
export function tracePathHighlight(trace: TraceResponse | undefined): TraceHighlight {
  const nodeNames = new Set<string>();
  const edgeKeys = new Set<string>();

  if (!trace || !trace.found || trace.paths.length === 0) {
    return { nodeNames, edgeKeys, active: false };
  }

  const shortest = trace.paths[0];
  for (const hop of shortest) {
    nodeNames.add(hop.from_name);
    nodeNames.add(hop.to_name);
    edgeKeys.add(edgeKey(hop.from_name, hop.to_name));
  }

  return { nodeNames, edgeKeys, active: nodeNames.size > 0 };
}
