/**
 * Risk-tier vocabulary + visual mapping for the impact overlay.
 *
 * The Seam engine groups blast-radius results into three tiers by distance:
 *   WILL_BREAK       (d=1) — direct dependents; a change WILL break these.
 *   LIKELY_AFFECTED  (d=2) — indirect; should be tested.
 *   MAY_NEED_TESTING (d≥3) — transitive; test if on a critical path.
 *
 * WHY a separate module: the tier→colour/label/precedence mapping is shared by
 * impactOverlay (node painting), the Legend, and SymbolNode (tier ring), so it
 * lives here once to avoid drift.
 */

/** The three risk tiers, ordered MOST → LEAST severe. */
export const RISK_TIERS = [
  "WILL_BREAK",
  "LIKELY_AFFECTED",
  "MAY_NEED_TESTING",
] as const;

export type RiskTier = (typeof RISK_TIERS)[number];

/** Tier → hex colour (dark-theme friendly): red → amber → slate. */
const TIER_COLOURS: Record<RiskTier, string> = {
  WILL_BREAK: "#f87171", // red-400 — will break
  LIKELY_AFFECTED: "#fbbf24", // amber-400 — likely affected
  MAY_NEED_TESTING: "#94a3b8", // slate-400 — may need testing
};

/** Tier → short human label for legends/badges. */
const TIER_LABELS: Record<RiskTier, string> = {
  WILL_BREAK: "Will break (d=1)",
  LIKELY_AFFECTED: "Likely affected (d=2)",
  MAY_NEED_TESTING: "May need testing (d≥3)",
};

/** Colour for a tier; null for an unknown/empty tier so callers can skip styling. */
export function tierColor(tier: string | null | undefined): string | null {
  if (!tier) return null;
  return TIER_COLOURS[tier as RiskTier] ?? null;
}

/** Human label for a tier; falls back to the raw string. */
export function tierLabel(tier: string): string {
  return TIER_LABELS[tier as RiskTier] ?? tier;
}

/**
 * Lower number = more severe. Used to resolve a node that appears in multiple
 * tiers (across upstream/downstream) — it keeps the most severe tier.
 * Unknown tiers sort last.
 */
export function tierRank(tier: string): number {
  const idx = (RISK_TIERS as readonly string[]).indexOf(tier);
  return idx === -1 ? RISK_TIERS.length : idx;
}

/** Return the more-severe of two tiers (used when merging upstream + downstream). */
export function moreSevereTier(a: string, b: string): string {
  return tierRank(a) <= tierRank(b) ? a : b;
}
