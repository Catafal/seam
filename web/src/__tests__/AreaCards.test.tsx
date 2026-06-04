/**
 * Tests for AreaCards (Phase 2.3 — functional area landing).
 *
 * Presentational only (no fetch): verifies cards render their name/counts/key
 * symbols, the show-tests toggle fires its callback, and clicking a card enters
 * that area.
 */

import { render, screen, fireEvent } from "@testing-library/react";
import { it, expect, vi } from "vitest";
import { AreaCards } from "../components/AreaCards";
import type { Area } from "../lib/deriveAreas";

const AREAS: Area[] = [
  {
    key: "pkg/indexer",
    name: "indexer",
    fileCount: 3,
    symbolCount: 12,
    keySymbols: ["index_one", "walk"],
    paths: ["pkg/indexer/a.py", "pkg/indexer/b.py", "pkg/indexer/c.py"],
  },
  {
    key: "pkg/query",
    name: "query",
    fileCount: 1,
    symbolCount: 4,
    keySymbols: [],
    paths: ["pkg/query/q.py"],
  },
];

it("renders one card per area with name, counts, and key symbols", () => {
  render(
    <AreaCards
      areas={AREAS}
      isLoading={false}
      includeTests={false}
      onToggleTests={() => {}}
      onEnterArea={() => {}}
    />,
  );
  expect(screen.getByText("indexer")).toBeInTheDocument();
  expect(screen.getByText("query")).toBeInTheDocument();
  expect(screen.getByText(/3 files · 12 symbols/)).toBeInTheDocument();
  expect(screen.getByText("index_one, walk")).toBeInTheDocument();
});

it("fires onToggleTests when the show-tests checkbox is clicked", () => {
  const onToggleTests = vi.fn();
  render(
    <AreaCards
      areas={AREAS}
      isLoading={false}
      includeTests={false}
      onToggleTests={onToggleTests}
      onEnterArea={() => {}}
    />,
  );
  fireEvent.click(screen.getByTestId("show-tests-toggle"));
  expect(onToggleTests).toHaveBeenCalledOnce();
});

it("enters an area when its card is clicked", () => {
  const onEnterArea = vi.fn();
  render(
    <AreaCards
      areas={AREAS}
      isLoading={false}
      includeTests={false}
      onToggleTests={() => {}}
      onEnterArea={onEnterArea}
    />,
  );
  fireEvent.click(screen.getByText("indexer"));
  expect(onEnterArea).toHaveBeenCalledWith(AREAS[0]);
});

it("shows an empty state when there are no areas", () => {
  render(
    <AreaCards
      areas={[]}
      isLoading={false}
      includeTests={false}
      onToggleTests={() => {}}
      onEnterArea={() => {}}
    />,
  );
  expect(screen.getByText(/no areas to show/i)).toBeInTheDocument();
});
