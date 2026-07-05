import { PNG } from "pngjs";

export interface PixelMetrics {
  width: number;
  height: number;
  backgroundRatio: number;
  nearWhiteRatio: number;
  nonBackgroundRatio: number;
  luminanceVariance: number;
  colorVariance: number;
}

export interface PixelThresholds {
  minWidth: number;
  minHeight: number;
  maxBackgroundRatio: number;
  maxNearWhiteRatio: number;
  minLuminanceVariance: number;
  minColorVariance: number;
  minNonBackgroundRatio: number;
}

export const TOPOLOGY_PIXEL_THRESHOLDS: PixelThresholds = {
  minWidth: 240,
  minHeight: 240,
  maxBackgroundRatio: 0.998,
  maxNearWhiteRatio: 0.25,
  minLuminanceVariance: 0.5,
  minColorVariance: 0.2,
  minNonBackgroundRatio: 0.002,
};

export interface PixelCheckResult {
  metrics: PixelMetrics;
  failures: string[];
}

function colorDistanceSquared(
  r: number,
  g: number,
  b: number,
  baseline: { r: number; g: number; b: number },
): number {
  return (
    (r - baseline.r) * (r - baseline.r) +
    (g - baseline.g) * (g - baseline.g) +
    (b - baseline.b) * (b - baseline.b)
  );
}

export function analyzePng(buffer: Buffer): PixelMetrics {
  const image = PNG.sync.read(buffer);
  const { width, height, data } = image;
  const totalPixels = width * height;

  const baseline = { r: data[0] ?? 0, g: data[1] ?? 0, b: data[2] ?? 0 };
  let backgroundLike = 0;
  let nearWhite = 0;
  let nonBackground = 0;
  let luminanceSum = 0;
  let luminanceSquaredSum = 0;
  let channelSum = 0;
  let channelSquaredSum = 0;

  for (let offset = 0; offset < data.length; offset += 4) {
    const r = data[offset];
    const g = data[offset + 1];
    const b = data[offset + 2];
    const luminance = 0.2126 * r + 0.7152 * g + 0.0722 * b;

    luminanceSum += luminance;
    luminanceSquaredSum += luminance * luminance;
    channelSum += r + g + b;
    channelSquaredSum += r * r + g * g + b * b;

    if (colorDistanceSquared(r, g, b, baseline) <= 9) {
      backgroundLike += 1;
    } else {
      nonBackground += 1;
    }

    if (r >= 245 && g >= 245 && b >= 245) {
      nearWhite += 1;
    }
  }

  const channelCount = totalPixels * 3;
  const luminanceMean = luminanceSum / totalPixels;
  const channelMean = channelSum / channelCount;

  return {
    width,
    height,
    backgroundRatio: backgroundLike / totalPixels,
    nearWhiteRatio: nearWhite / totalPixels,
    nonBackgroundRatio: nonBackground / totalPixels,
    luminanceVariance: luminanceSquaredSum / totalPixels - luminanceMean * luminanceMean,
    colorVariance: channelSquaredSum / channelCount - channelMean * channelMean,
  };
}

export function checkTopologyPixels(
  buffer: Buffer,
  thresholds: PixelThresholds = TOPOLOGY_PIXEL_THRESHOLDS,
): PixelCheckResult {
  const metrics = analyzePng(buffer);
  const failures: string[] = [];

  if (metrics.width < thresholds.minWidth) {
    failures.push(`canvas width ${metrics.width} < ${thresholds.minWidth}`);
  }
  if (metrics.height < thresholds.minHeight) {
    failures.push(`canvas height ${metrics.height} < ${thresholds.minHeight}`);
  }
  if (metrics.backgroundRatio > thresholds.maxBackgroundRatio) {
    failures.push(
      `background ratio ${metrics.backgroundRatio.toFixed(4)} > ${thresholds.maxBackgroundRatio}`,
    );
  }
  if (metrics.nearWhiteRatio > thresholds.maxNearWhiteRatio) {
    failures.push(
      `near-white ratio ${metrics.nearWhiteRatio.toFixed(4)} > ${thresholds.maxNearWhiteRatio}`,
    );
  }
  if (metrics.luminanceVariance < thresholds.minLuminanceVariance) {
    failures.push(
      `luminance variance ${metrics.luminanceVariance.toFixed(4)} < ${thresholds.minLuminanceVariance}`,
    );
  }
  if (metrics.colorVariance < thresholds.minColorVariance) {
    failures.push(
      `color variance ${metrics.colorVariance.toFixed(4)} < ${thresholds.minColorVariance}`,
    );
  }
  if (metrics.nonBackgroundRatio < thresholds.minNonBackgroundRatio) {
    failures.push(
      `non-background ratio ${metrics.nonBackgroundRatio.toFixed(4)} < ${thresholds.minNonBackgroundRatio}`,
    );
  }

  return { metrics, failures };
}
