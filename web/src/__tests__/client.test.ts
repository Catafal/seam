/**
 * TDD tests for web/src/api/client.ts.
 *
 * The client is a thin typed fetch wrapper over the Seam Explorer API.
 * Tests verify:
 * - apiFetch constructs the correct URL and parses JSON
 * - Non-2xx responses throw with the error detail from the response body
 * - Network errors (fetch rejection) propagate as-is
 * - baseURL defaults to '' so both dev-proxy and same-origin prod work
 */

// We mock globalThis.fetch so these tests never hit a real server.
import { apiFetch } from "../api/client";

// Helper: create a minimal Response-like object
function makeResponse(
  body: unknown,
  status: number = 200,
): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as unknown as Response;
}

describe("apiFetch", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("calls fetch with the correct URL and default options", async () => {
    fetchMock.mockResolvedValueOnce(makeResponse({ ok: true }));
    await apiFetch("/api/status");
    expect(fetchMock).toHaveBeenCalledWith("/api/status", expect.any(Object));
  });

  it("returns parsed JSON on a 200 response", async () => {
    const payload = { root: "/tmp", symbol_count: 42 };
    fetchMock.mockResolvedValueOnce(makeResponse(payload));
    const result = await apiFetch<typeof payload>("/api/status");
    expect(result).toEqual(payload);
  });

  it("throws an error with the detail message on a 4xx response", async () => {
    const errorBody = { detail: { code: "NO_INDEX", message: "Run seam init first." } };
    fetchMock.mockResolvedValueOnce(makeResponse(errorBody, 503));
    await expect(apiFetch("/api/status")).rejects.toThrow("Run seam init first.");
  });

  it("throws an error with the status code when detail is absent", async () => {
    fetchMock.mockResolvedValueOnce(makeResponse({}, 500));
    await expect(apiFetch("/api/status")).rejects.toThrow("HTTP 500");
  });

  it("propagates network errors (fetch rejection) unchanged", async () => {
    const netErr = new TypeError("Failed to fetch");
    fetchMock.mockRejectedValueOnce(netErr);
    await expect(apiFetch("/api/status")).rejects.toThrow("Failed to fetch");
  });

  it("passes query params when provided", async () => {
    fetchMock.mockResolvedValueOnce(makeResponse({ results: [] }));
    await apiFetch("/api/search", { params: { q: "hello", limit: "20" } });
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/search?q=hello&limit=20",
      expect.any(Object),
    );
  });
});
