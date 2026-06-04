/**
 * deriveAreas — turn the flat /api/structure symbol list into a handful of
 * functional "area" cards for the Overview landing.
 *
 * The idea: in a well-organized repo the top-level source directories ARE the
 * functional areas (seam/indexer = parse→index, seam/query = read path, …). We
 * don't need a clustering algorithm — the folder layout already encodes function.
 * So we group by directory, UNWRAP the dominant package (e.g. everything lives
 * under `seam/`, so its children become the areas, not one giant `seam` card),
 * hide tests by default, and bucket the graph's hub symbols into each area as a
 * "key symbols" hint.
 *
 * Honest limit: this is only as meaningful as the repo's structure. A junk-drawer
 * repo (one big `src/`) degrades to "whatever folders exist" — that's the case
 * that would need clustering, which we deliberately don't do here.
 *
 * Pure + framework-free → unit-tested in isolation. Each area carries the exact
 * set of file `paths` it owns, so the treemap can be scoped to it precisely
 * (including the synthetic `core` bucket, which a path-prefix couldn't express).
 */

import type { StructureSymbol, HubSymbol } from "../api/schema-types";

/** Directory names treated as tests — hidden from areas unless includeTests. */
const TEST_SEGMENTS = new Set(["tests", "test", "__tests__", "spec", "specs", "e2e"]);

/** Unwrap the top package only when it holds at least this fraction of symbols. */
const DOMINANT_THRESHOLD = 0.6;

/** Max key-symbol hints shown per area card. */
const MAX_KEY_SYMBOLS = 3;

export interface Area {
  /** Stable id; a path prefix for normal areas or "<pkg>/__core__" for loose files. */
  key: string;
  /** Display name — the area's directory name (or "core" for loose package files). */
  name: string;
  fileCount: number;
  symbolCount: number;
  /** Up to 3 hub symbol names in this area, highest-degree first. */
  keySymbols: string[];
  /** Exact file paths owned by this area — scopes the treemap drill-down. */
  paths: string[];
}

/** First path segment ("seam/indexer/db.py" → "seam"). */
function firstSeg(path: string): string {
  const i = path.indexOf("/");
  return i === -1 ? path : path.slice(0, i);
}

/** A path is a test path if ANY of its segments is a known test directory name. */
function isTestPath(path: string): boolean {
  return path.split("/").some((seg) => TEST_SEGMENTS.has(seg));
}

/**
 * Build the functional areas from the structure list + graph hubs.
 *
 * @param symbols     flat /api/structure rows
 * @param hubs        /api/hubs rows (pre-sorted by degree desc; each carries a path)
 * @param opts.includeTests  when false (default behavior of the caller), test dirs
 *                           are excluded from areas
 * @returns areas sorted by symbol count desc; [] for an empty/all-filtered index
 */
export function deriveAreas(
  symbols: StructureSymbol[],
  hubs: HubSymbol[],
  opts: { includeTests: boolean },
): Area[] {
  const filtered = opts.includeTests
    ? symbols
    : symbols.filter((s) => !isTestPath(s.path));
  if (filtered.length === 0) return [];

  // Top-level segment counts → find the dominant package.
  const topCount = new Map<string, number>();
  for (const s of filtered) {
    const top = firstSeg(s.path);
    topCount.set(top, (topCount.get(top) ?? 0) + 1);
  }
  const total = filtered.length;
  let dominant = "";
  let dominantCount = -1;
  for (const [seg, c] of topCount) {
    if (c > dominantCount) {
      dominant = seg;
      dominantCount = c;
    }
  }
  // Only unwrap if the dominant package both dominates AND has sub-directories
  // (paths ≥3 segments) — otherwise unwrapping yields a single useless "core".
  const dominantHasChildren = filtered.some((s) => {
    const parts = s.path.split("/").filter(Boolean);
    return parts[0] === dominant && parts.length >= 3;
  });
  const unwrap = dominantCount >= DOMINANT_THRESHOLD * total && dominantHasChildren;

  // Map a single path to its area {key, name}. The synthetic "core" bucket holds
  // loose files directly under the unwrapped package (e.g. seam/config.py).
  const areaOf = (path: string): { key: string; name: string } => {
    const parts = path.split("/").filter(Boolean);
    const top = parts[0];
    if (unwrap && top === dominant) {
      if (parts.length >= 3) return { key: `${parts[0]}/${parts[1]}`, name: parts[1] };
      return { key: `${dominant}/__core__`, name: "core" };
    }
    return { key: top, name: top };
  };

  // Aggregate symbols into areas (each symbol assigned to exactly one area).
  const byArea = new Map<
    string,
    { name: string; paths: Set<string>; symbolCount: number }
  >();
  for (const s of filtered) {
    const { key, name } = areaOf(s.path);
    let a = byArea.get(key);
    if (!a) {
      a = { name, paths: new Set(), symbolCount: 0 };
      byArea.set(key, a);
    }
    a.paths.add(s.path);
    a.symbolCount += 1;
  }

  // Bucket hubs (already degree-sorted) into their area as key-symbol hints.
  const keyBy = new Map<string, string[]>();
  for (const h of hubs) {
    if (!h.path) continue;
    const { key } = areaOf(h.path);
    if (!byArea.has(key)) continue; // hub in a filtered-out (test) area → skip
    const list = keyBy.get(key) ?? [];
    if (list.length < MAX_KEY_SYMBOLS && !list.includes(h.name)) {
      list.push(h.name);
      keyBy.set(key, list);
    }
  }

  const areas: Area[] = [];
  for (const [key, a] of byArea) {
    areas.push({
      key,
      name: a.name,
      fileCount: a.paths.size,
      symbolCount: a.symbolCount,
      keySymbols: keyBy.get(key) ?? [],
      paths: [...a.paths].sort(),
    });
  }
  // Largest area first; stable tie-break on name.
  areas.sort((x, y) => y.symbolCount - x.symbolCount || x.name.localeCompare(y.name));
  return areas;
}
