import { expect, test, type Page, type TestInfo } from "@playwright/test";

import { checkTopologyPixels } from "./helpers/pixelAnalyzer";
import { startExplorerServer, type ExplorerServer } from "./helpers/serverHarness";

declare global {
  interface Window {
    __SEAM_TOPOLOGY_VISUAL_QA__?: {
      selectFirstNode: () => string | null;
      reset: () => void;
    };
  }
}

let server: ExplorerServer;
const blockedExternalRequests = new WeakMap<Page, string[]>();

test.beforeAll(async () => {
  server = await startExplorerServer();
});

test.afterAll(async () => {
  await server?.stop();
});

test.beforeEach(async ({ page }, testInfo) => {
  const blocked: string[] = [];
  blockedExternalRequests.set(page, blocked);

  await page.route("**/*", async (route) => {
    const requestUrl = new URL(route.request().url());
    const local =
      ["http:", "https:"].includes(requestUrl.protocol) &&
      ["127.0.0.1", "localhost"].includes(requestUrl.hostname);
    const browserInternal = ["about:", "blob:", "data:"].includes(requestUrl.protocol);

    if (local || browserInternal) {
      await route.continue();
      return;
    }

    blocked.push(route.request().url());
    await route.abort("blockedbyclient");
  });

});

test.afterEach(async ({ page }, testInfo) => {
  const blocked = blockedExternalRequests.get(page) ?? [];
  await testInfo.attach("blocked-external-requests", {
    body: Buffer.from(blocked.join("\n")),
    contentType: "text/plain",
  });
  expect(blocked).toEqual([]);
});

function collectBrowserFailures(page: Page): string[] {
  const failures: string[] = [];

  page.on("console", (message) => {
    if (message.type() === "error") {
      failures.push(`console error: ${message.text()}`);
    }
  });
  page.on("pageerror", (error) => {
    failures.push(`page error: ${error.message}`);
  });
  page.on("requestfailed", (request) => {
    const url = request.url();
    if (url.startsWith(server.url)) {
      failures.push(`request failed: ${url} ${request.failure()?.errorText ?? ""}`);
    }
  });

  return failures;
}

async function openTopology(page: Page): Promise<void> {
  await page.goto(`${server.url}/?seam-visual-qa=1`);
  await page.getByRole("tab", { name: /topology/i }).click();
  await expect(page.getByTestId("topology-canvas").locator("canvas")).toBeVisible();
  await page.waitForTimeout(1_200);
}

async function assertWebglAvailable(page: Page): Promise<void> {
  const available = await page.evaluate(() => {
    const canvas = document.createElement("canvas");
    return Boolean(canvas.getContext("webgl2") ?? canvas.getContext("webgl"));
  });
  test.skip(!available, "Chromium does not expose WebGL in this environment");
}

async function assertCanvasPixels(page: Page, testInfo: TestInfo): Promise<void> {
  const canvas = page.getByTestId("topology-canvas").locator("canvas");
  const screenshot = await canvas.screenshot();
  const result = checkTopologyPixels(screenshot);

  await testInfo.attach("topology-canvas.png", {
    body: screenshot,
    contentType: "image/png",
  });
  await testInfo.attach("topology-pixel-metrics.json", {
    body: Buffer.from(JSON.stringify(result.metrics, null, 2)),
    contentType: "application/json",
  });

  expect(result.failures).toEqual([]);
}

test("Topology renders a nonblank scene in desktop and mobile Chromium", async ({ page }, testInfo) => {
  const failures = collectBrowserFailures(page);

  await assertWebglAvailable(page);
  await openTopology(page);
  await assertCanvasPixels(page, testInfo);

  expect(failures).toEqual([]);
});

test("Topology selection and reset stay inside the 3D surface", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "chromium-desktop", "interaction invariant is covered once");

  const failures = collectBrowserFailures(page);
  await assertWebglAvailable(page);
  await openTopology(page);

  const selected = await page.evaluate(() => window.__SEAM_TOPOLOGY_VISUAL_QA__?.selectFirstNode() ?? null);
  expect(selected).toBeTruthy();

  const root = page.getByTestId("topology-root");
  await expect(root).toHaveAttribute("data-selected", selected as string);
  await expect(page.getByLabel("Symbol detail panel")).toBeVisible();
  await expect(page).toHaveURL(/seam-visual-qa=1/);

  await page.keyboard.press("Escape");
  await expect(root).toHaveAttribute("data-selected", "");
  await expect(page.getByLabel("Symbol detail panel")).toHaveCount(0);

  await assertCanvasPixels(page, testInfo);
  expect(failures).toEqual([]);
});

test("Topology layout failure shows an error instead of a blank canvas", async ({ page }) => {
  test.skip(test.info().project.name !== "chromium-desktop", "error invariant is covered once");

  await page.route("**/api/graph/layout**", async (route) => {
    await route.fulfill({
      status: 500,
      contentType: "application/json",
      body: JSON.stringify({ detail: { code: "VISUAL_QA_FAILURE", message: "forced failure" } }),
    });
  });

  await page.goto(`${server.url}/?seam-visual-qa=1`);
  await page.getByRole("tab", { name: /topology/i }).click();

  await expect(page.getByText("Failed to load constellation layout.")).toBeVisible();
  await expect(page.getByTestId("topology-canvas")).toHaveCount(0);
});
