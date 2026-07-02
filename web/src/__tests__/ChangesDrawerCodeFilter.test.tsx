/**
 * A4 (issue #217) review-follow-up — ChangesDrawer filters non-code files from
 * BOTH the changed-symbols list AND the "New files" list.
 *
 * The original A4 slice filtered `changed_symbols` but left `new_files`
 * unfiltered, so untracked docs/logs/configs (e.g. docs/prd/x.md, logs/run.log)
 * still leaked into the drawer — the exact noise A4 set out to remove. These
 * tests lock the new-files filtering in.
 *
 * Strategy: mock useChanges so no network fires; assert the rendered DOM only
 * contains code new-files, and that a mix of all-non-code new-files collapses
 * to the empty-state message.
 */

import { render, screen } from "@testing-library/react";
import { vi, describe, it, expect, beforeEach } from "vitest";

// Mock the data hook — we only test the drawer's filtering/rendering behavior.
const mockUseChanges = vi.fn();
vi.mock("../api/hooks", () => ({
  useChanges: (...args: unknown[]) => mockUseChanges(...args),
}));

import { ChangesDrawer } from "../components/ChangesDrawer";

function renderDrawer() {
  return render(
    <ChangesDrawer open onClose={() => {}} onSelectSymbol={() => {}} />,
  );
}

describe("ChangesDrawer – new_files code filtering (A4 follow-up)", () => {
  beforeEach(() => {
    mockUseChanges.mockReset();
  });

  it("renders only code new-files, dropping docs/logs/config paths", () => {
    mockUseChanges.mockReturnValue({
      data: {
        risk_level: "low",
        changed_symbols: [],
        new_files: [
          "seam/indexer/new_mod.py", // code — keep
          "web/src/App.tsx", // code — keep
          "docs/prd/phase-a.md", // non-code — drop
          "logs/run.log", // non-code — drop
          "Makefile", // no extension — drop
        ],
        partial: false,
      },
      isLoading: false,
      isError: false,
      error: null,
    });

    renderDrawer();

    // Header count reflects only the 2 code files.
    expect(screen.getByText("New files (2)")).toBeTruthy();
    // Code files present.
    expect(screen.getByText("seam/indexer/new_mod.py")).toBeTruthy();
    expect(screen.getByText("web/src/App.tsx")).toBeTruthy();
    // Non-code files absent.
    expect(screen.queryByText("docs/prd/phase-a.md")).toBeNull();
    expect(screen.queryByText("logs/run.log")).toBeNull();
    expect(screen.queryByText("Makefile")).toBeNull();
  });

  it("shows the empty-state when every new file is non-code", () => {
    mockUseChanges.mockReturnValue({
      data: {
        risk_level: "none",
        changed_symbols: [],
        new_files: ["docs/x.md", "logs/y.log", ".gitignore"],
        partial: false,
      },
      isLoading: false,
      isError: false,
      error: null,
    });

    renderDrawer();

    expect(screen.getByText("No changes in the working tree.")).toBeTruthy();
    expect(screen.queryByText(/New files/)).toBeNull();
  });
});
