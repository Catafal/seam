# PRD - Phase 11: Explorer Topology Visual QA

> Status: ready for agent.
> Created: 2026-07-04.
> GitHub issue: https://github.com/Catafal/seam/issues/309.
> Tracker label: `ready-for-agent`.
> Source roadmap: Phase 11 codebase-memory status matrix and follow-up RFC roadmap.

## Problem Statement

Seam Explorer now has a polished 3D Topology surface, but the acceptance layer is still weaker
than the product claim. Unit tests and component tests cover helpers, state transitions, API
payloads, and error states. They do not prove the thing the user actually sees: a nonblank,
legible, correctly framed WebGL canvas on real desktop and mobile browser viewports.

This gap was explicitly accepted earlier when the 3D Topology surface was still stabilizing. The
original 3D design deferred browser visual testing because jsdom cannot render WebGL and the first
slice needed to ship the experience. The later polish PRD also documented that bloom, additive
materials, black-hole prevention, and white-out prevention were visual concerns that required
manual or screenshot verification. That was appropriate for first delivery, but it is no longer
enough.

From the user's perspective, the risk is simple: the Topology tab can pass every normal test while
still rendering a blank canvas, an overexposed wash, a collapsed layout, an offscreen constellation,
unreadable overlay text, or broken interaction in a real browser. This is particularly risky
because the 3D path depends on lazy-loaded bundles, WebGL availability, canvas sizing, browser
layout, animation frames, postprocessing, and real pointer events. Those are exactly the parts that
unit tests do not execute.

The current roadmap says the 3D visual acceptance item is partially shipped: the surface exists,
but no browser screenshot or canvas-pixel checks prove nonblank, legible rendering. Since the
protocol edges, infra graph, graph artifacts, and cross-repo roadmap families have now landed, this
is the next high-value roadmap slice.

The problem is not to redesign the Topology UI. The problem is to make its browser-level quality
provable, repeatable, and safe to run in CI without introducing network dependencies or brittle
pixel-perfect snapshots.

## Solution

Add a focused Explorer visual QA suite around the 3D Topology surface.

The suite should launch Seam Explorer against a deterministic tiny indexed repository, open the app
in a real browser, navigate to Topology, and verify the visual and interaction contract from the
outside:

1. the Topology tab renders the WebGL canvas;
2. the canvas has stable dimensions and is not collapsed;
3. the canvas is not blank after layout data loads and animation frames settle;
4. the rendered scene has meaningful non-background pixel variance;
5. HUD, filter panel, and side panels do not overlap incoherently;
6. desktop and mobile viewports both show a usable Topology surface;
7. node selection isolates a neighborhood instead of navigating away;
8. empty-canvas click or Escape resets selection;
9. API/data failures show a visible error state instead of a blank surface;
10. generated screenshot artifacts are available for debugging when the test fails.

The test should not enforce exact pixel snapshots. Seam's 3D scene uses GPU rendering, bloom,
anti-aliasing, browser compositing, and animation timing, all of which can vary across machines.
The correct acceptance model is invariant-based: canvas present, nonzero size, nonblank pixels,
reasonable color variance, expected app text visible, stable view mode, no console/page errors, and
expected interaction state visible.

The implementation should keep Seam's local-first trust model intact. Browser tests may start local
servers and make localhost HTTP requests, but must not call external networks. Query paths remain
read-only. The suite should be an optional browser QA target at first, then become part of the
normal gate only when it is reliable in CI.

## User Stories

1. As a Seam maintainer, I want browser-level proof that the Topology canvas is nonblank, so that
   helper tests cannot hide a broken WebGL render.
2. As a Seam maintainer, I want desktop screenshots captured on failure, so that I can debug visual
   regressions without reproducing them manually.
3. As a Seam maintainer, I want mobile screenshots captured on failure, so that responsive failures
   are visible in CI artifacts.
4. As a Seam maintainer, I want the test to wait for data load and animation frames, so that it does
   not sample the canvas before the first real render.
5. As a Seam maintainer, I want canvas pixel checks based on invariants rather than exact snapshots,
   so that GPU differences do not make the suite flaky.
6. As a Seam maintainer, I want the canvas bounding box checked, so that zero-height or hidden
   canvases fail immediately.
7. As a Seam maintainer, I want the rendered scene checked for non-background pixel variance, so
   that a canvas painted only with the background color fails.
8. As a Seam maintainer, I want the rendered scene checked for a minimum amount of visible content,
   so that an offscreen or over-dim constellation fails.
9. As a Seam maintainer, I want the test to reject a mostly pure-white canvas, so that bloom
   white-out regressions are caught.
10. As a Seam maintainer, I want the test to reject a mostly black or background-only canvas, so
    that blank WebGL and collapsed scene regressions are caught.
11. As a Seam maintainer, I want browser console errors collected, so that lazy import, WebGL,
    postprocessing, and runtime React failures fail loudly.
12. As a Seam maintainer, I want network failures collected, so that broken API calls are surfaced
    with useful context.
13. As a Seam maintainer, I want the Topology tab reached through the real tab UI, so that routing,
    lazy loading, and state transitions are covered together.
14. As a Seam maintainer, I want the test fixture to build a graph with enough nodes and edges, so
    that the visual checks exercise a real constellation rather than a one-dot toy.
15. As a Seam maintainer, I want the fixture to stay deterministic, so that visual acceptance does
    not depend on the user's local repository shape.
16. As a Seam maintainer, I want the fixture index created locally during the test, so that the
    suite does not depend on checked-in database files.
17. As a Seam maintainer, I want the test to run against the built Explorer bundle or the same app
    served by the production local server, so that it validates what users receive from `seam serve`.
18. As a Seam maintainer, I want the test to avoid external network calls, so that it does not weaken
    Seam's no-egress story.
19. As a Seam maintainer, I want localhost-only browser traffic allowed and documented, so that QA
    network behavior is explicit.
20. As a Seam maintainer, I want the browser harness to clean up every process it starts, so that
    failed tests do not leave dev servers running.
21. As a Seam maintainer, I want the test to use an available free port, so that parallel local work
    does not collide with a fixed port.
22. As a Seam maintainer, I want reduced-motion behavior to be testable or at least non-disruptive,
    so that animation does not make screenshots unstable.
23. As a Seam maintainer, I want desktop layout checks, so that the filter panel, HUD, canvas, and
    detail panel do not overlap in the common wide viewport.
24. As a Seam maintainer, I want mobile layout checks, so that the Topology surface remains usable
    when horizontal space is constrained.
25. As a Seam maintainer, I want text containment checks for visible controls, so that labels do not
    overflow buttons or panels.
26. As a Seam maintainer, I want the HUD visible on desktop, so that users know the scene loaded and
    can read counts.
27. As a Seam maintainer, I want mobile acceptance to allow responsive simplification, so that the
    test asserts usability rather than desktop parity.
28. As a Seam maintainer, I want a node-selection test, so that click-to-isolate remains the primary
    Topology interaction.
29. As a Seam maintainer, I want selection to keep the app in Topology mode, so that the old broken
    3D-to-2D navigation path cannot return.
30. As a Seam maintainer, I want Escape and empty click reset behavior covered, so that isolate is a
    reversible lens.
31. As a Seam maintainer, I want the failure-state test to prove visible errors, so that API failures
    do not produce a blank canvas.
32. As a Seam maintainer, I want the browser suite command documented, so that agents know when to
    run it.
33. As a Seam maintainer, I want CI wiring documented separately from first implementation if
    required, so that a flaky new browser suite does not destabilize every PR prematurely.
34. As an AI coding agent, I want the PRD to specify exact acceptance signals, so that I can
    implement the suite without guessing what "looks good" means.
35. As an AI coding agent, I want visual QA results to be machine-readable where practical, so that
    I can summarize failures in a PR.
36. As an AI coding agent, I want screenshot files named by viewport and scenario, so that failure
    artifacts are easy to inspect.
37. As an AI coding agent, I want the suite to fail closed when the browser cannot render WebGL in
    CI, so that missing graphics support is not silently treated as a pass.
38. As an AI coding agent, I want local setup instructions for browser dependencies, so that I can
    reproduce failures quickly.
39. As an Explorer user, I want the Topology tab to load a real constellation every time, so that I
    can trust the visual overview.
40. As an Explorer user, I want the first viewport to show the product surface, not a loading shell,
    so that the UI feels ready.
41. As an Explorer user, I want the scene framed inside the available canvas, so that I do not need
    to manually search for the graph.
42. As an Explorer user, I want controls to stay readable over the canvas, so that visual style does
    not block operation.
43. As an Explorer user, I want mobile Topology to degrade gracefully, so that the app is not broken
    on smaller screens.
44. As an Explorer user, I want clicking a node to visibly change the scene, so that the interaction
    has clear feedback.
45. As an Explorer user, I want resetting selection to visibly restore the full field, so that I can
    continue exploring.
46. As a future visual-regression implementer, I want invariant tests first, so that exact snapshots
    can be added later only where stable.
47. As a future CI maintainer, I want the browser QA target isolated, so that it can be promoted into
    the main gate after reliability is proven.
48. As a future release maintainer, I want Explorer browser QA before release packaging, so that
    bundled web assets are not shipped broken.
49. As a future frontend contributor, I want documented visual invariants, so that rendering changes
    know which qualities must be preserved.
50. As a future roadmap maintainer, I want this PRD to close the partially-shipped visual QA item,
    so that the Phase 11 matrix reflects real acceptance status.

## Implementation Decisions

- Treat this as a QA and acceptance feature, not a visual redesign.
- Use a real browser automation framework capable of Chromium/WebGL rendering.
- Add browser automation as a development/test dependency only; do not add runtime product
  dependencies.
- Prefer production-like local serving over component-only rendering. The suite should validate
  the actual Explorer app and local API integration.
- Use a deterministic temporary repository fixture with enough functions, classes, calls, imports,
  and clusters to produce a meaningful Topology scene.
- Build or serve the Explorer app through the same local server behavior users exercise, unless the
  implementation proves a development server is necessary for reliable source-map diagnostics.
- Start local servers on loopback only.
- Allocate ports dynamically or through a collision-safe helper.
- Ensure every spawned process is terminated in success and failure paths.
- Navigate through the real Overview/Symbol/Topology tab UI.
- Wait for the Topology canvas to exist, have a nonzero bounding box, and survive at least a small
  number of animation frames before sampling pixels.
- Read the canvas through browser APIs when possible. If WebGL security or compositing constraints
  block direct reads, use screenshot-region sampling around the canvas bounding box.
- Use invariant thresholds rather than pixel-perfect snapshots.
- Define nonblank as a combination of non-background pixels, luminance variance, and color-channel
  variance.
- Define white-out as an excessive percentage of near-white pixels inside the canvas region.
- Define black/background blankness as an excessive percentage of pixels matching the expected
  canvas background or near-black values.
- Keep thresholds intentionally broad at first, then tighten only after repeated CI evidence.
- Capture screenshots for desktop loaded, desktop selected, desktop reset, mobile loaded, and error
  state when relevant.
- Do not commit generated screenshots as golden files in the first slice.
- Store failure artifacts under the test output directory so CI can upload them.
- Collect browser console errors, page errors, and failed localhost network requests.
- Fail on uncaught app/runtime errors.
- Ignore known benign browser warnings only by explicit allowlist with comments explaining why.
- Do not hide WebGL/browser setup failures with unconditional skips.
- If CI cannot support WebGL yet, the PRD should produce a separate issue for CI provisioning rather
  than marking visual QA complete.
- Test desktop at a wide viewport representative of normal development use.
- Test mobile at a narrow viewport representative of constrained browser use.
- Desktop acceptance should require the HUD and canvas to be visible simultaneously.
- Mobile acceptance should require visible Topology content and no incoherent overlap, but may allow
  controls to stack or collapse according to existing responsive behavior.
- Add stable test selectors only where accessible roles/names are insufficient.
- Prefer accessible roles, labels, and visible text for navigation and controls.
- Use a real pointer click against the canvas region for interaction checks.
- If direct node clicking is too brittle because node positions are generated by WebGL, allow a
  deterministic test hook only in non-production test mode. The preferred approach is to click a
  known visible node by using layout data and canvas projection if feasible.
- Selection acceptance should prove visible state change, not just internal React state.
- Reset acceptance should prove selected detail state disappears or full-scene counts return.
- Error acceptance should intercept or stub the layout API and assert a visible error message rather
  than a mounted blank canvas.
- Document the new test command in the contributor/test documentation.
- Keep the normal fast frontend unit test command unchanged.
- Add a distinct browser QA command so agents can choose it when touching Explorer visual surfaces.
- Do not change MCP tool contracts.
- Do not change SQLite schema.
- Do not change index extraction.
- Do not change query semantics.
- Do not require embeddings or semantic search for the fixture.
- Do not require external services or remote browsers.
- Update the Phase 11 status matrix after the implementation lands so Explorer visual QA moves from
  partially shipped to shipped.

## Testing Decisions

- Good tests assert user-visible behavior and product invariants, not implementation details of the
  renderer.
- Unit tests remain appropriate for pure layout math, color mapping, edge geometry, selection helper
  behavior, and tab state.
- Browser tests own the visual facts that unit tests cannot prove: nonblank canvas, visible layout,
  real navigation, real pointer/keyboard interaction, and responsive containment.
- The first browser suite should include a desktop loaded-scene test.
- The first browser suite should include a mobile loaded-scene test.
- The first browser suite should include a desktop selection/reset test.
- The first browser suite should include an API failure state test.
- The pixel analyzer should be isolated behind a small test helper with a simple interface: sample a
  canvas or screenshot region and return background ratio, near-white ratio, luminance variance,
  color variance, dimensions, and a pass/fail explanation.
- The server/process harness should be isolated behind a small test helper with a simple interface:
  build fixture, index fixture, start Explorer, return URL, and clean up.
- Tests should use a deterministic fixture rather than the Seam repo itself, because the Seam repo's
  graph shape changes constantly and would make visual thresholds drift.
- Tests should still run against the built/bundled app path, because the product risk is what users
  see through `seam serve`.
- Browser test failures should emit enough structured context to make the failure actionable:
  viewport, scenario, canvas size, ratios/variance, console errors, request failures, and artifact
  paths.
- The visual QA command should be run when touching Topology, Explorer app shell, layout API, web
  packaging, or static asset serving.
- The normal backend gate should not become dependent on browser downloads until the suite has a
  stable CI story.

## Out of Scope

- Redesigning the Topology visual language.
- Re-tuning bloom, colors, node sizing, or layout math except where a tiny testability hook is
  required.
- Adding pixel-perfect snapshot regression tests.
- Adding a remote visual testing service.
- Adding external network dependencies.
- Adding runtime product dependencies.
- Reintroducing the old 2D/3D topology toggle.
- Changing the Explorer information architecture.
- Changing the layout API contract.
- Changing MCP, CLI graph tools, or SQLite schema.
- Testing every browser engine in the first slice.
- Testing every possible GPU backend in the first slice.

## Further Notes

- Roadmap status: protocol edges, infra graph, graph artifact lifecycle, and cross-repo analysis have
  merged. The remaining high-signal Phase 11 gap is Explorer visual acceptance.
- Current evidence: existing tests cover the web API, layout data, helper math, tab state, and
  error branches, but there is no browser-level Playwright suite and no canvas pixel check.
- The original 3D design explicitly deferred Playwright because the surface was new. The later polish
  work made the surface stable enough for a browser QA layer.
- The acceptance threshold should be practical: catch blank, collapsed, offscreen, overexposed, and
  interaction-broken states without blocking harmless GPU-level differences.
- This PRD should be broken into implementation issues before code: browser harness, pixel analyzer,
  desktop/mobile loaded checks, interaction/error checks, docs/CI follow-through.
