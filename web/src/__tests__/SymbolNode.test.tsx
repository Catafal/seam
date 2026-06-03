/**
 * TDD tests for web/src/components/SymbolNode.tsx.
 *
 * SymbolNode is a custom React Flow node card. It must:
 * - Render the symbol name prominently
 * - Render a truncated signature when present
 * - Show a cluster colour stripe (via background color)
 * - Emphasize the center node with a ring
 * - Show a kind icon (lucide-react icon by kind string)
 *
 * NOTE: React Flow wraps custom nodes in its own context and passes
 * `data` via `NodeProps`. We test SymbolNode in isolation by rendering
 * it directly with the shape it expects from GraphCanvas.
 */

import { render, screen } from "@testing-library/react";
import { SymbolNode } from "../components/SymbolNode";
import type { SymbolNodeData } from "../components/SymbolNode";

// Build a minimal NodeProps-like object accepted by SymbolNode.
// React Flow's full NodeProps includes many fields; we only need `data`.
function renderSymbolNode(data: SymbolNodeData) {
  // SymbolNode is exported as a named export and accepts { data } as its prop.
  return render(<SymbolNode data={data} />);
}

const BASE_DATA: SymbolNodeData = {
  name: "parse",
  kind: "function",
  signature: "def parse(code: str) -> Tree",
  cluster_id: 1,
  cluster_label: "parser",
  definition_count: 1,
  isCenter: false,
};

describe("SymbolNode", () => {
  it("renders the symbol name", () => {
    renderSymbolNode(BASE_DATA);
    expect(screen.getByText("parse")).toBeInTheDocument();
  });

  it("renders the signature when present", () => {
    renderSymbolNode(BASE_DATA);
    // Signature may be truncated by CSS but must appear in the DOM
    expect(
      screen.getByText(/def parse\(code: str\) -> Tree/),
    ).toBeInTheDocument();
  });

  it("does NOT render a signature element when signature is null", () => {
    const data: SymbolNodeData = { ...BASE_DATA, signature: null };
    renderSymbolNode(data);
    // There should be no element containing the signature class / text
    expect(screen.queryByText(/def parse/)).not.toBeInTheDocument();
  });

  it("applies center-node emphasis when isCenter=true", () => {
    const data: SymbolNodeData = { ...BASE_DATA, isCenter: true };
    const { container } = renderSymbolNode(data);
    // The outer wrapper should have a ring class or aria attribute marking it as center
    const root = container.firstChild as HTMLElement;
    expect(root.className).toMatch(/ring/);
  });

  it("does NOT apply ring when isCenter=false", () => {
    const { container } = renderSymbolNode(BASE_DATA);
    const root = container.firstChild as HTMLElement;
    expect(root.className).not.toMatch(/ring-2/);
  });

  it("renders with a cluster colour stripe element when cluster_id is set", () => {
    const { container } = renderSymbolNode(BASE_DATA);
    // The stripe is a dedicated <div> with an inline background-color
    const stripes = container.querySelectorAll("[data-testid='cluster-stripe']");
    expect(stripes.length).toBe(1);
  });

  it("renders without a cluster stripe when cluster_id is null", () => {
    const data: SymbolNodeData = {
      ...BASE_DATA,
      cluster_id: null,
      cluster_label: null,
    };
    const { container } = renderSymbolNode(data);
    const stripes = container.querySelectorAll("[data-testid='cluster-stripe']");
    expect(stripes.length).toBe(0);
  });

  it("renders with kind text for non-function kinds", () => {
    const data: SymbolNodeData = { ...BASE_DATA, kind: "class" };
    renderSymbolNode(data);
    // The node must exist and render
    expect(screen.getByText("parse")).toBeInTheDocument();
  });

  it("renders definition count badge when count > 1 (homonyms)", () => {
    const data: SymbolNodeData = { ...BASE_DATA, definition_count: 3 };
    renderSymbolNode(data);
    // Should show the count as a badge so the user knows there are multiple definitions
    expect(screen.getByText("×3")).toBeInTheDocument();
  });

  it("does not render count badge when definition_count is 1", () => {
    renderSymbolNode(BASE_DATA); // definition_count: 1
    expect(screen.queryByText(/×\d/)).not.toBeInTheDocument();
  });
});
