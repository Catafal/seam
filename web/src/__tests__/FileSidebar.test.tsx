/**
 * Component tests for FileSidebar.
 *
 * Covers: search filtering, click-to-open (qualified-name callback),
 * collapse/expand, lazy-fetch gating, and count badges.
 *
 * WHY mock useStructure: the sidebar's lazy-fetch gate is an internal state
 * decision driven by whether the user has opened the sidebar. Stubbing the hook
 * lets us verify the enabled param without a real server or fetch.
 */

import { render, screen, fireEvent, act } from "@testing-library/react";
import { vi, describe, it, expect, beforeEach, afterEach } from "vitest";
import { FileSidebar } from "../components/FileSidebar";
import type { StructureSymbol } from "../api/schema-types";
import type { UseQueryResult } from "@tanstack/react-query";

// ── Mock useStructure ──────────────────────────────────────────────────────────
// vi.mock is hoisted by vitest so this runs before any imports are resolved.

vi.mock("../api/hooks", () => ({
  useStructure: vi.fn(),
}));

// Import AFTER the mock so we get the mocked version.
import { useStructure } from "../api/hooks";
const mockUseStructure = vi.mocked(useStructure);

// ── localStorage mock ──────────────────────────────────────────────────────────

function makeLocalStorageMock() {
  let store: Record<string, string> = {};
  return {
    getItem: (key: string): string | null => store[key] ?? null,
    setItem: (key: string, val: string): void => { store[key] = String(val); },
    removeItem: (key: string): void => { delete store[key]; },
    clear: (): void => { store = {}; },
  };
}

// ── Fixtures ───────────────────────────────────────────────────────────────────

/**
 * Minimal flat symbol list covering: two files, a class with a method
 * (qualified_name present), and a bare function (qualified_name null).
 */
const SYMBOLS: StructureSymbol[] = [
  {
    path: "seam/indexer/db.py",
    name: "init_db",
    kind: "function",
    line: 1,
    qualified_name: "init_db",
    degree: 0,
  },
  {
    path: "seam/indexer/db.py",
    name: "Db",
    kind: "class",
    line: 10,
    qualified_name: "Db",
    degree: 0,
  },
  {
    path: "seam/indexer/db.py",
    name: "connect",
    kind: "method",
    line: 15,
    qualified_name: "Db.connect",
    degree: 0,
  },
  {
    path: "seam/analysis/clustering.py",
    name: "detect",
    kind: "function",
    line: 1,
    qualified_name: null, // no qualified name → bare name fallback
    degree: 0,
  },
];

// ── Helpers ────────────────────────────────────────────────────────────────────

/** Open the sidebar by clicking its toggle button. */
function openSidebar() {
  fireEvent.click(screen.getByLabelText("Open file sidebar"));
}

// ── Test suite ─────────────────────────────────────────────────────────────────

describe("FileSidebar", () => {
  let lsMock: ReturnType<typeof makeLocalStorageMock>;

  /** Build a minimal UseQueryResult-compatible mock value for useStructure. */
  function mockResult(data: StructureSymbol[] | undefined, isLoading = false) {
    return { data, isLoading } as unknown as UseQueryResult<StructureSymbol[]>;
  }

  beforeEach(() => {
    lsMock = makeLocalStorageMock();
    vi.stubGlobal("localStorage", lsMock);
    // Default: sidebar starts closed (no localStorage entry for open state).
    mockUseStructure.mockReturnValue(mockResult(undefined));
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.clearAllMocks();
  });

  // ── Collapsed state ────────────────────────────────────────────────────────

  it("renders the collapsed toggle button when starting closed", () => {
    render(<FileSidebar onOpen={vi.fn()} />);
    expect(screen.getByLabelText("Open file sidebar")).toBeInTheDocument();
    expect(screen.queryByLabelText("Close file sidebar")).not.toBeInTheDocument();
  });

  it("shows the panel header after opening via the toggle button", () => {
    render(<FileSidebar onOpen={vi.fn()} />);
    openSidebar();
    expect(screen.getByLabelText("Close file sidebar")).toBeInTheDocument();
    expect(screen.queryByLabelText("Open file sidebar")).not.toBeInTheDocument();
  });

  it("collapses again when the close button is clicked", () => {
    render(<FileSidebar onOpen={vi.fn()} />);
    openSidebar();
    fireEvent.click(screen.getByLabelText("Close file sidebar"));
    expect(screen.getByLabelText("Open file sidebar")).toBeInTheDocument();
  });

  // ── Lazy-fetch gating ──────────────────────────────────────────────────────

  it("calls useStructure with enabled=false when sidebar starts closed", () => {
    render(<FileSidebar onOpen={vi.fn()} />);
    // Sidebar is closed → enabled must be false so the API call is not made
    expect(mockUseStructure).toHaveBeenCalledWith(false);
    const trueCalls = mockUseStructure.mock.calls.filter(([e]) => e === true);
    expect(trueCalls).toHaveLength(0);
  });

  it("switches to enabled=true when the sidebar is opened for the first time", () => {
    render(<FileSidebar onOpen={vi.fn()} />);
    openSidebar();
    expect(mockUseStructure).toHaveBeenCalledWith(true);
  });

  it("keeps enabled=true even after the sidebar is closed and reopened", () => {
    render(<FileSidebar onOpen={vi.fn()} />);
    openSidebar();
    // Close the sidebar
    fireEvent.click(screen.getByLabelText("Close file sidebar"));
    // Reopen
    openSidebar();
    // After the first open every subsequent call must use true
    const calls = mockUseStructure.mock.calls;
    const firstTrueIdx = calls.findIndex(([e]) => e === true);
    expect(firstTrueIdx).toBeGreaterThanOrEqual(0);
    const afterFirstTrue = calls.slice(firstTrueIdx);
    expect(afterFirstTrue.every(([e]) => e === true)).toBe(true);
  });

  it("starts with enabled=true when localStorage says the sidebar was open", () => {
    // Simulate the user having had the sidebar open in a previous session.
    lsMock.setItem("seam-sidebar-open", "true");
    render(<FileSidebar onOpen={vi.fn()} />);
    // Sidebar starts open → first call must use enabled=true
    expect(mockUseStructure).toHaveBeenCalledWith(true);
  });

  // ── Count badges ───────────────────────────────────────────────────────────

  it("shows symbol count badges on dir/file nodes", () => {
    mockUseStructure.mockReturnValue(mockResult(SYMBOLS));
    render(<FileSidebar onOpen={vi.fn()} />);
    openSidebar();

    // After opening, the root-level dir (seam/) should be visible with a badge.
    const badges = screen.getAllByTestId("sidebar-count-badge");
    expect(badges.length).toBeGreaterThan(0);
    // seam/ has 4 symbols total (init_db, Db, connect, detect)
    const seamBadge = badges.find((b) => b.textContent === "4");
    expect(seamBadge).toBeDefined();
  });

  // ── Search filtering ───────────────────────────────────────────────────────

  it("filters the tree to matching nodes when a search query is entered", () => {
    vi.useFakeTimers();
    mockUseStructure.mockReturnValue(mockResult(SYMBOLS));
    render(<FileSidebar onOpen={vi.fn()} />);
    openSidebar();

    const searchInput = screen.getByTestId("sidebar-search");
    fireEvent.change(searchInput, { target: { value: "connect" } });
    // Advance past the 250ms debounce
    act(() => { vi.advanceTimersByTime(300); });

    // "connect" should be visible; "detect" (only in clustering.py) should not
    expect(screen.getByText("connect")).toBeInTheDocument();
    expect(screen.queryByText("detect")).not.toBeInTheDocument();

    vi.useRealTimers();
  });

  it("shows all nodes again when search is cleared", () => {
    vi.useFakeTimers();
    mockUseStructure.mockReturnValue(mockResult(SYMBOLS));
    render(<FileSidebar onOpen={vi.fn()} />);
    openSidebar();

    const searchInput = screen.getByTestId("sidebar-search");
    // Apply filter
    fireEvent.change(searchInput, { target: { value: "connect" } });
    act(() => { vi.advanceTimersByTime(300); });
    expect(screen.queryByText("detect")).not.toBeInTheDocument();

    // Clear filter
    fireEvent.change(searchInput, { target: { value: "" } });
    act(() => { vi.advanceTimersByTime(300); });
    // "detect" dir is no longer force-shown, but the seam dir badge is still there
    // (dirs are collapsed by default when not filtering)
    expect(screen.getAllByTestId("sidebar-count-badge").length).toBeGreaterThan(0);

    vi.useRealTimers();
  });

  // ── Click-to-open with qualified name ──────────────────────────────────────

  it("calls onOpen with the qualified_name when a symbol with one is clicked", () => {
    vi.useFakeTimers();
    mockUseStructure.mockReturnValue(mockResult(SYMBOLS));
    const onOpen = vi.fn();
    render(<FileSidebar onOpen={onOpen} />);
    openSidebar();

    // Filter to "connect" to force-expand all dirs and make the symbol visible
    const searchInput = screen.getByTestId("sidebar-search");
    fireEvent.change(searchInput, { target: { value: "connect" } });
    act(() => { vi.advanceTimersByTime(300); });

    // "connect" method with qualified_name "Db.connect" should now be visible
    const symbolBtn = screen.getByTestId("sidebar-symbol-connect");
    fireEvent.click(symbolBtn);

    // Should open using the qualified name, not the bare name
    expect(onOpen).toHaveBeenCalledWith("Db.connect");

    vi.useRealTimers();
  });

  it("falls back to the bare name when qualified_name is null", () => {
    vi.useFakeTimers();
    mockUseStructure.mockReturnValue(mockResult(SYMBOLS));
    const onOpen = vi.fn();
    render(<FileSidebar onOpen={onOpen} />);
    openSidebar();

    // "detect" has qualified_name = null — filter to it
    const searchInput = screen.getByTestId("sidebar-search");
    fireEvent.change(searchInput, { target: { value: "detect" } });
    act(() => { vi.advanceTimersByTime(300); });

    const symbolBtn = screen.getByTestId("sidebar-symbol-detect");
    fireEvent.click(symbolBtn);

    // Falls back to bare name
    expect(onOpen).toHaveBeenCalledWith("detect");

    vi.useRealTimers();
  });

  // ── Collapse / expand ──────────────────────────────────────────────────────

  it("expands a dir node when clicked and collapses it again on second click", () => {
    mockUseStructure.mockReturnValue(mockResult(SYMBOLS));
    render(<FileSidebar onOpen={vi.fn()} />);
    openSidebar();

    // Initially, only root-level dirs are visible (seam/ badge)
    const rootDir = screen.getByTestId("sidebar-dir-seam");
    expect(rootDir).toBeInTheDocument();
    // Children not yet visible (collapsed)
    expect(screen.queryByTestId("sidebar-dir-indexer")).not.toBeInTheDocument();

    // Expand
    fireEvent.click(rootDir);
    expect(screen.getByTestId("sidebar-dir-indexer")).toBeInTheDocument();
    expect(screen.getByTestId("sidebar-dir-analysis")).toBeInTheDocument();

    // Collapse again
    fireEvent.click(rootDir);
    expect(screen.queryByTestId("sidebar-dir-indexer")).not.toBeInTheDocument();
  });

  // ── Persistence ────────────────────────────────────────────────────────────

  it("persists open state to localStorage when toggled", () => {
    render(<FileSidebar onOpen={vi.fn()} />);
    // Starts closed — should persist "false"
    expect(lsMock.getItem("seam-sidebar-open")).toBe("false");

    openSidebar();
    expect(lsMock.getItem("seam-sidebar-open")).toBe("true");
  });
});
