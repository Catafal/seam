/**
 * Squarified treemap layout (Bruls, Huizing & van Wijk, 2000).
 *
 * Lays out a SINGLE level of children inside a rectangle, choosing rows that keep
 * rectangles close to square (readable) rather than thin slivers. The Overview
 * renders one level at a time and drills down on click, so single-level layout is
 * all we need.
 *
 * Pure + framework-free → unit-tested (areas proportional to value, within bounds).
 */

export interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface TreemapItem<T> {
  value: number;
  node: T;
}

export interface PlacedItem<T> {
  node: T;
  rect: Rect;
}

/** Worst (largest) aspect ratio in a row of areas laid along `side`. */
function worstRatio(areas: number[], side: number): number {
  if (areas.length === 0) return Infinity;
  const sum = areas.reduce((a, b) => a + b, 0);
  const max = Math.max(...areas);
  const min = Math.min(...areas);
  const s2 = side * side;
  const sum2 = sum * sum;
  return Math.max((s2 * max) / sum2, sum2 / (s2 * min));
}

/**
 * Squarified treemap of `items` within `rect`. Returns one PlacedItem per item
 * with value > 0 (zero/negative values are dropped). Total placed area ≈ rect area.
 */
export function squarify<T>(items: TreemapItem<T>[], rect: Rect): PlacedItem<T>[] {
  const valid = items.filter((it) => it.value > 0);
  if (valid.length === 0 || rect.w <= 0 || rect.h <= 0) return [];

  const sorted = [...valid].sort((a, b) => b.value - a.value);
  const total = sorted.reduce((s, it) => s + it.value, 0);
  const scale = (rect.w * rect.h) / total;
  const scaled = sorted.map((it) => ({ node: it.node, area: it.value * scale }));

  const placed: PlacedItem<T>[] = [];
  let { x, y, w, h } = rect;
  let row: { node: T; area: number }[] = [];
  let rowAreas: number[] = [];

  const layoutRow = () => {
    const rowArea = rowAreas.reduce((a, b) => a + b, 0);
    if (w >= h) {
      // Column on the left edge: width = rowArea / column height.
      const colW = rowArea / h;
      let yy = y;
      for (let k = 0; k < row.length; k++) {
        const cellH = rowAreas[k] / colW;
        placed.push({ node: row[k].node, rect: { x, y: yy, w: colW, h: cellH } });
        yy += cellH;
      }
      x += colW;
      w -= colW;
    } else {
      // Row on the top edge: height = rowArea / row width.
      const rowH = rowArea / w;
      let xx = x;
      for (let k = 0; k < row.length; k++) {
        const cellW = rowAreas[k] / rowH;
        placed.push({ node: row[k].node, rect: { x: xx, y, w: cellW, h: rowH } });
        xx += cellW;
      }
      y += rowH;
      h -= rowH;
    }
    row = [];
    rowAreas = [];
  };

  for (const it of scaled) {
    const side = Math.min(w, h);
    const cur = worstRatio(rowAreas, side);
    const next = worstRatio([...rowAreas, it.area], side);
    // Start a new row when adding this item would worsen the worst aspect ratio.
    if (row.length > 0 && next > cur) {
      layoutRow();
    }
    row.push(it);
    rowAreas.push(it.area);
  }
  if (row.length > 0) layoutRow();

  return placed;
}
