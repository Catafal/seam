/**
 * Typed fetch wrapper for the Seam Explorer API.
 *
 * Design decisions:
 * - baseURL is '' (empty string): works with the Vite dev proxy (/api → localhost:7420)
 *   AND with the same-origin production setup where seam serve serves both the API and
 *   the SPA from the same 127.0.0.1:7420 origin.
 * - All API paths start with '/api/' — the caller always passes the full path.
 * - Error handling: non-2xx responses throw an Error with the detail.message from the
 *   FastAPI error body, falling back to "HTTP <status>" if the body lacks detail.
 * - Generic <T>: the caller declares the expected response shape; no runtime validation
 *   (the Pydantic backend is the source of truth).
 */

/** Options for apiFetch — query params are passed as a plain record. */
export interface FetchOptions extends RequestInit {
  /** Query parameters appended to the URL (e.g. { q: "foo", limit: "20" }). */
  params?: Record<string, string | number | boolean | undefined>;
}

/**
 * Typed fetch wrapper over the Seam Explorer REST API.
 *
 * @param path  Absolute path starting with '/api/…'
 * @param opts  Standard RequestInit + optional params record for query string
 * @returns     Parsed JSON body cast to T
 * @throws      Error when the response is not 2xx, with message from the API error body
 */
export async function apiFetch<T = unknown>(
  path: string,
  opts: FetchOptions = {},
): Promise<T> {
  const { params, ...fetchInit } = opts;

  // Build the full URL — starts with an empty base so it works dev and prod.
  let url = path;
  if (params) {
    // Filter out undefined values so optional params don't appear as "key=undefined"
    const defined = Object.entries(params).filter(
      ([, v]) => v !== undefined,
    ) as [string, string | number | boolean][];
    if (defined.length > 0) {
      const qs = new URLSearchParams(
        defined.map(([k, v]) => [k, String(v)]),
      ).toString();
      url = `${path}?${qs}`;
    }
  }

  const response = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...fetchInit,
  });

  if (!response.ok) {
    // Try to extract a human-readable message from the FastAPI error body.
    // FastAPI wraps errors as: { "detail": { "code": "...", "message": "..." } }
    // Fall back to a generic "HTTP <status>" message if the body differs.
    let message = `HTTP ${response.status}`;
    try {
      const body = await response.json();
      const detail = body?.detail;
      if (typeof detail === "string") {
        message = detail;
      } else if (detail && typeof detail.message === "string") {
        message = detail.message;
      }
    } catch {
      // Body wasn't JSON — keep the generic message
    }
    throw new Error(message);
  }

  return response.json() as Promise<T>;
}
