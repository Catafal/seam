import { expect, test } from "@playwright/test";
import { PNG } from "pngjs";

import { checkTopologyPixels } from "./pixelAnalyzer";

function solidPng(width: number, height: number, color: [number, number, number]): Buffer {
  const png = new PNG({ width, height });
  for (let offset = 0; offset < png.data.length; offset += 4) {
    png.data[offset] = color[0];
    png.data[offset + 1] = color[1];
    png.data[offset + 2] = color[2];
    png.data[offset + 3] = 255;
  }
  return PNG.sync.write(png);
}

function starFieldPng(): Buffer {
  const png = new PNG({ width: 300, height: 300 });
  for (let offset = 0; offset < png.data.length; offset += 4) {
    png.data[offset] = 4;
    png.data[offset + 1] = 16;
    png.data[offset + 2] = 15;
    png.data[offset + 3] = 255;
  }

  for (let i = 0; i < 900; i += 1) {
    const x = (i * 37) % png.width;
    const y = (i * 53) % png.height;
    const offset = (y * png.width + x) * 4;
    png.data[offset] = 30 + (i % 180);
    png.data[offset + 1] = 120 + (i % 100);
    png.data[offset + 2] = 180 + (i % 70);
  }

  return PNG.sync.write(png);
}

test.describe("topology pixel analyzer", () => {
  test("fails blank canvases", () => {
    const result = checkTopologyPixels(solidPng(300, 300, [4, 16, 15]));

    expect(result.failures.some((failure) => failure.includes("background ratio"))).toBe(true);
    expect(result.failures.some((failure) => failure.includes("luminance variance"))).toBe(true);
  });

  test("fails white-out canvases", () => {
    const result = checkTopologyPixels(solidPng(300, 300, [255, 255, 255]));

    expect(result.failures.some((failure) => failure.includes("near-white ratio"))).toBe(true);
  });

  test("accepts a varied nonblank field", () => {
    const result = checkTopologyPixels(starFieldPng());

    expect(result.failures).toEqual([]);
    expect(result.metrics.nonBackgroundRatio).toBeGreaterThan(0.002);
  });
});
