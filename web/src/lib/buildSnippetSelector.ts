/**
 * buildSnippetSelector — pure helper for the Source panel in DetailPanel.
 *
 * Converts a selected symbol name + its first indexed definition into a
 * SnippetSelector that the useSnippet hook accepts.
 *
 * WHY file+line over symbol-only: the API always prefers a precise (file, line)
 * selector to disambiguate homonyms. When the panel is already showing a specific
 * definition (firstDef), the Source section should show that exact definition's
 * source — not whatever the API might pick for a bare symbol name. This is the
 * "homonym's source matches the shown metadata" contract from the PRD.
 *
 * WHY return undefined for falsy symbol: useSnippet's `enabled` gate prevents
 * a fetch when the selector is undefined. The caller passes `!!selector` as the
 * `enabled` flag — simpler than checking `symbol` separately.
 */

import type { SnippetSelector } from "../api/hooks";
import type { SymbolDefinition } from "../api/schema-types";

/**
 * Build a SnippetSelector from a symbol name and its first displayed definition.
 *
 * Returns:
 *   - file+line selector when both symbol and firstDef are provided (most precise)
 *   - symbol-only selector when firstDef is missing (API resolves by name)
 *   - undefined when symbol is falsy (caller should disable the hook)
 */
export function buildSnippetSelector(
  symbol: string | null | undefined,
  firstDef: SymbolDefinition | null | undefined,
): SnippetSelector | undefined {
  // Guard: nothing to select when symbol is absent or blank
  if (!symbol || !symbol.trim()) return undefined;

  if (firstDef) {
    // Precise selector: include file+line so the API returns the exact definition
    // the panel is already showing (homonym-safe)
    return { symbol, file: firstDef.file, line: firstDef.line };
  }

  // Fallback: symbol-only — API resolves by name (may be ambiguous)
  return { symbol };
}
