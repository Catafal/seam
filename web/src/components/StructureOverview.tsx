/**
 * StructureOverview — the Overview tab: functional-area cards that drill into the
 * structure treemap.
 *
 * Two states:
 *   - no area selected → <AreaCards> (the functional "blackboxes")
 *   - an area selected → <TreemapCanvas scoped to that area> with a "back to areas"
 *     crumb. The treemap is the detail VIEW inside a blackbox, not the entry point.
 *
 * Owns the two bits of UI state (selected area + include-tests toggle) and feeds
 * deriveAreas; everything else is delegated.
 */

import { useMemo, useState } from "react";
import { useStructure, useHubs } from "../api/hooks";
import { deriveAreas, type Area } from "../lib/deriveAreas";
import { AreaCards } from "./AreaCards";
import { TreemapCanvas } from "./TreemapCanvas";

export interface StructureOverviewProps {
  /** Open a symbol's neighborhood graph (treemap leaf click). */
  onSelectSymbol: (name: string) => void;
}

export function StructureOverview({ onSelectSymbol }: StructureOverviewProps) {
  const [includeTests, setIncludeTests] = useState(false);
  const [area, setArea] = useState<Area | null>(null);

  const { data: symbols, isLoading } = useStructure(true);
  // High limit so every area gets hub coverage when we bucket by path.
  const { data: hubs } = useHubs(60);

  const areas = useMemo(
    () => deriveAreas(symbols ?? [], hubs ?? [], { includeTests }),
    [symbols, hubs, includeTests],
  );

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
