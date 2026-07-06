/**
 * NodeIdentityCard tests (#361).
 *
 * The card is the crash-proof replacement for the old fetching NodeDetailPanel.
 * These tests lock down the contract that matters: it renders identity from the
 * node alone (NO fetch), shows the connection count, and the explicit
 * "Open in neighborhood →" button hands off with the node's name.
 */

import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { NodeIdentityCard } from "../components/NodeIdentityCard";
import type { LayoutNode } from "../lib/layoutTypes";

function makeNode(overrides: Partial<LayoutNode> = {}): LayoutNode {
  return {
    id: 1,
    name: "ImportMapping",
    label: "class",
    file_path: "seam/analysis/imports.py",
    x: 0,
    y: 0,
    z: 0,
    size: 4,
    color: "#a855f7",
    ...overrides,
  } as LayoutNode;
}

describe("NodeIdentityCard", () => {
  it("renders name, kind, and file basename from the node (no fetch)", () => {
    render(
      <NodeIdentityCard
        node={makeNode()}
        connectionCount={12}
        onOpenInNeighborhood={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByText("ImportMapping")).toBeInTheDocument();
    expect(screen.getByText("class")).toBeInTheDocument();
    // Basename only, not the full path
    expect(screen.getByText("imports.py")).toBeInTheDocument();
  });

  it("shows the connection count with correct pluralization", () => {
    const { rerender } = render(
      <NodeIdentityCard
        node={makeNode()}
        connectionCount={12}
        onOpenInNeighborhood={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByText("12")).toBeInTheDocument();
    expect(screen.getByText("connections")).toBeInTheDocument();

    rerender(
      <NodeIdentityCard
        node={makeNode()}
        connectionCount={1}
        onOpenInNeighborhood={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByText("connection")).toBeInTheDocument();
  });

  it("the Open in neighborhood button hands off with the node name", () => {
    const onOpen = vi.fn();
    render(
      <NodeIdentityCard
        node={makeNode({ name: "Db.connect" })}
        connectionCount={3}
        onOpenInNeighborhood={onOpen}
        onClose={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByText(/open in neighborhood/i));
    expect(onOpen).toHaveBeenCalledTimes(1);
    expect(onOpen).toHaveBeenCalledWith("Db.connect");
  });

  it("close button calls onClose", () => {
    const onClose = vi.fn();
    render(
      <NodeIdentityCard
        node={makeNode()}
        connectionCount={0}
        onOpenInNeighborhood={vi.fn()}
        onClose={onClose}
      />,
    );
    fireEvent.click(screen.getByLabelText("Clear selection"));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("degrades gracefully when file_path is null (no crash, no basename row)", () => {
    render(
      <NodeIdentityCard
        node={makeNode({ file_path: null })}
        connectionCount={5}
        onOpenInNeighborhood={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    // Still renders identity without throwing
    expect(screen.getByText("ImportMapping")).toBeInTheDocument();
  });
});
