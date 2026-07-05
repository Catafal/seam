# Phase 11 Topology Visual QA Issues

Parent PRD: GitHub issue #309, "Phase 11 Topology Visual QA".

## S1 — Playwright Harness And Deterministic Explorer Fixture (#310)

Add a browser-test harness under `web/`, create a deterministic temporary fixture repo, index it with Seam, and serve the built Explorer over loopback.

Why: visual QA must exercise the same Python server and built SPA that users run, not a component-only mock.

## S2 — Canvas Pixel Invariant Analyzer (#311)

Add PNG-level checks for canvas dimensions, blank scenes, white-out, luminance variance, color variance, and non-background pixel ratio.

Why: screenshots alone are weak as automated acceptance; numeric pixel invariants catch the common WebGL failures without brittle snapshot diffs.

## S3 — Desktop And Mobile Loaded-Scene Checks (#312)

Run Chromium desktop and mobile viewport checks that open Topology and prove the canvas is visible and nonblank.

Why: the 3D surface can regress differently across viewport constraints, especially around panel/canvas sizing.

## S4 — Selection Reset And Error-State Browser Checks (#313)

Verify node selection opens the detail panel, Escape resets selection inside 3D, and `/api/graph/layout` failure shows an error instead of leaving a blank canvas.

Why: Topology must remain self-contained and fail visibly when layout data cannot load.

## S5 — Docs CI Wiring And Final Verification (#314)

Document local commands, add optional CI workflow wiring, and run relevant local checks before merging.

Why: future changes need an obvious command and artifact trail for browser regressions.
