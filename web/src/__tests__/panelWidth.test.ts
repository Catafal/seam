/**
 * Unit tests for the shared readPanelWidth / clampPanelWidth helpers.
 *
 * readPanelWidth is extracted from ConstellationTab (where it was inline)
 * into ResizeHandle.tsx so both 2D (App.tsx) and 3D (ConstellationTab.tsx)
 * share one implementation — no duplication, no drift.
 *
 * WHY test localStorage errors: storage may be disabled in private browsing
 * or capacity-exceeded; the helper must never throw and must always return a
 * valid number in range [PANEL_MIN_W, PANEL_MAX_W].
 *
 * WHY use a manual localStorage mock: the jsdom environment provided by vitest
 * emits a "--localstorage-file" warning that leaves the native localStorage
 * with broken method stubs (setItem / clear are not functions). We stub the
 * global with a real in-memory implementation so tests are deterministic and
 * environment-independent.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  readPanelWidth,
  clampPanelWidth,
  PANEL_MIN_W,
  PANEL_MAX_W,
} from "../components/ResizeHandle";

// ── localStorage mock ──────────────────────────────────────────────────────────

/** Build a fresh in-memory localStorage stand-in for each test. */
function makeLocalStorageMock() {
  let store: Record<string, string> = {};
  return {
    getItem: (key: string): string | null => store[key] ?? null,
    setItem: (key: string, value: string): void => { store[key] = String(value); },
    removeItem: (key: string): void => { delete store[key]; },
    clear: (): void => { store = {}; },
  };
}

// ── clampPanelWidth ────────────────────────────────────────────────────────────

describe("clampPanelWidth", () => {
  it("clamps below PANEL_MIN_W up to PANEL_MIN_W", () => {
    expect(clampPanelWidth(0)).toBe(PANEL_MIN_W);
    expect(clampPanelWidth(PANEL_MIN_W - 1)).toBe(PANEL_MIN_W);
  });

  it("clamps above PANEL_MAX_W down to PANEL_MAX_W", () => {
    expect(clampPanelWidth(9999)).toBe(PANEL_MAX_W);
    expect(clampPanelWidth(PANEL_MAX_W + 1)).toBe(PANEL_MAX_W);
  });

  it("passes through values exactly at the bounds", () => {
    expect(clampPanelWidth(PANEL_MIN_W)).toBe(PANEL_MIN_W);
    expect(clampPanelWidth(PANEL_MAX_W)).toBe(PANEL_MAX_W);
  });

  it("passes through a mid-range value unchanged", () => {
    const mid = Math.round((PANEL_MIN_W + PANEL_MAX_W) / 2);
    expect(clampPanelWidth(mid)).toBe(mid);
  });
});

// ── readPanelWidth ─────────────────────────────────────────────────────────────

describe("readPanelWidth", () => {
  let storageMock: ReturnType<typeof makeLocalStorageMock>;

  beforeEach(() => {
    storageMock = makeLocalStorageMock();
    // Replace the global localStorage with our in-memory mock
    vi.stubGlobal("localStorage", storageMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("returns the fallback when localStorage has no entry for the key", () => {
    expect(readPanelWidth("missing-key", 250)).toBe(250);
  });

  it("reads and returns a stored value within range", () => {
    storageMock.setItem("test-key", "300");
    expect(readPanelWidth("test-key", 200)).toBe(300);
  });

  it("clamps a stored value that exceeds PANEL_MAX_W", () => {
    storageMock.setItem("test-key", "9999");
    expect(readPanelWidth("test-key", 200)).toBe(PANEL_MAX_W);
  });

  it("clamps a stored value below PANEL_MIN_W", () => {
    storageMock.setItem("test-key", "10");
    expect(readPanelWidth("test-key", 200)).toBe(PANEL_MIN_W);
  });

  it("returns the fallback for a non-numeric stored value (NaN guard)", () => {
    storageMock.setItem("test-key", "not-a-number");
    // Number("not-a-number") = NaN — must fall back, not return NaN
    const result = readPanelWidth("test-key", 200);
    expect(result).toBe(200);
  });

  it("returns the fallback when localStorage.getItem throws", () => {
    vi.stubGlobal("localStorage", {
      ...storageMock,
      getItem: () => { throw new Error("storage disabled"); },
    });
    expect(readPanelWidth("any-key", 300)).toBe(300);
  });

  it("the 2D and 3D keys are independent (different keys → different values)", () => {
    storageMock.setItem("seam-2d-detail-w", "260");
    storageMock.setItem("seam-right-w", "320");
    expect(readPanelWidth("seam-2d-detail-w", 288)).toBe(260);
    expect(readPanelWidth("seam-right-w", 280)).toBe(320);
  });

  it("returns the fallback as-is when the key is absent (no extra clamping)", () => {
    // The caller (useState initializer) is responsible for using a sane default.
    // readPanelWidth does NOT clamp the fallback itself — it's already in range.
    const result = readPanelWidth("no-key", 250);
    expect(result).toBe(250);
  });
});
