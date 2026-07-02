/**
 * StructureOverview — the Overview tab: functional-area cards that drill into the
 * structure treemap.
 *
 * Two states:
 *   - no area selected → <AreaCards> (the functional "blackboxes")
 *   - an area selected → <TreemapCanvas scoped to that area> with a "back to areas"
 *     crumb. The treemap is the detail VIEW inside a blackbox, not the entry point.
 *
 * Owns the include-tests toggle and the selected area; delegates everything else.
 *
 * B1: areas derived via useAreas (single derivation site shared with the landing).
 * B1: accepts optional initialArea so the landing can pre-drill into a specific area.
 */

import { useState } from "react";
import { useAreas } from "../api/hooks";
import type { Area } from "../lib/deriveAreas";
import { AreaCards } from "./AreaCards";
import { TreemapCanvas } from "./TreemapCanvas";

export interface StructureOverviewProps {
  /** Open a symbol's neighborhood graph (treemap leaf click). */
  onSelectSymbol: (name: string) => void;
  /**
   * Pre-select an area on mount — used when the landing area card is clicked so
   * the Overview opens directly into the scoped treemap rather than the cards list.
   * Only consumed on mount (useState initial value); changes after mount are ignored.
   */
  initialArea?: Area | null;
}

export function StructureOverview({ onSelectSymbol, initialArea }: StructureOverviewProps) {
  const [includeTests, setIncludeTests] = useState(false);
  // initialArea is only read on mount — the user can navigate back to the cards list
  // from within this component, so local state owns the selected area from that point.
  const [area, setArea] = useState<Area | null>(initialArea ?? null);

  // B1: single derivation site — same hook the landing uses.
  const { areas, isLoading } = useAreas({ includeTests });

  if (area) {
    return (
      <TreemapCanvas
        scopePaths={area.paths}
        scopeName={area.name}
        onBack={() => setArea(null)}
        onSelectSymbol={onSelectSymbol}
      />
    );
  }

  return (
    <AreaCards
      areas={areas}
      isLoading={isLoading}
      includeTests={includeTests}
      onToggleTests={() => setIncludeTests((v) => !v)}
      onEnterArea={setArea}
    />
  );
}
