/**
 * Impact-overlay helper: turn an ImpactResponse into a name → risk-tier map.
 *
 * The overlay paints each affected symbol by its risk tier. A symbol can appear
 * in BOTH upstream and downstream (and across tiers); we keep the MOST severe
 * tier so the worst-case risk is what reads on the canvas.
 *
 * Pure + framework-free → unit-tested in isolation. The canvas decides which
 * mapped names are on-canvas (recolour) vs off-canvas (append as faint nodes).
 */

import type { ImpactResponse } from "../api/schema-types";
import { moreSevereTier } from "./riskTier";

/**
 * Collapse an impact result to one tier per symbol name (most-severe wins).
 * Returns an empty map for undefined / not-found / empty results.
 */
export function impactTierMap(impact: ImpactResponse | undefined): Map<string, string> {
  const map = new Map<string, string>();
  if (!impact) return map;

  for (const group of [impact.upstream, impact.downstream]) {
    if (!group) continue;
    // group is { TIER: ImpactEntry[] } — the key IS the tier.
    for (const [tier, entries] of Object.entries(group)) {
      for (const entry of entries) {
        const existing = map.get(entry.name);
        map.set(entry.name, existing ? moreSevereTier(existing, tier) : tier);
      }
    }
  }
  return map;
}
