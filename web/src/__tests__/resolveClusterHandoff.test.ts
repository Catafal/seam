/**
 * TDD tests for resolveClusterHandoff (C3).
 *
 * Pure function: clicked cluster → neighborhood hand-off target (symbol name).
 *
 * Contract:
 *   - representative present → return representative
 *   - representative null, label present → return label (graceful fallback)
 *   - both null → return null (caller must not navigate)
 */

import { resolveClusterHandoff } from "../lib/resolveClusterHandoff";
import type { ConstellationCluster } from "../api/schema-types";

function makeCluster(
  representative: string | null,
  label: string | null = null,
  cluster_id = 1,
  size = 10,
): ConstellationCluster {
  return { cluster_id, size, label, representative };
}

describe("resolveClusterHandoff", () => {
  it("returns the representative when present", () => {
    const cluster = makeCluster("Indexer.run", "Indexer");
    expect(resolveClusterHandoff(cluster)).toBe("Indexer.run");
  });

  it("returns the representative even when label is null", () => {
    const cluster = makeCluster("Parser.parse", null);
    expect(resolveClusterHandoff(cluster)).toBe("Parser.parse");
  });

  it("falls back to label when representative is null and label is present", () => {
    const cluster = makeCluster(null, "CLI");
    expect(resolveClusterHandoff(cluster)).toBe("CLI");
  });

  it("returns null when both representative and label are null", () => {
    const cluster = makeCluster(null, null);
    expect(resolveClusterHandoff(cluster)).toBeNull();
  });

  it("does not return an empty string — treats empty representative as absent", () => {
    // An empty string representative should fall back to the label
    const cluster = makeCluster("", "Parsers");
    expect(resolveClusterHandoff(cluster)).toBe("Parsers");
  });

  it("does not return an empty string — treats empty label as absent", () => {
    // An empty string label is treated as absent; returns null when rep also empty
    const cluster = makeCluster("", "");
    expect(resolveClusterHandoff(cluster)).toBeNull();
  });

  it("is pure — multiple calls with the same cluster return the same value", () => {
    const cluster = makeCluster("DB.connect", "Database");
    expect(resolveClusterHandoff(cluster)).toBe("DB.connect");
    expect(resolveClusterHandoff(cluster)).toBe("DB.connect");
  });
});
