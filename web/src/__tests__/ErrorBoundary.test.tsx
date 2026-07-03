/**
 * TDD tests for the ErrorBoundary class component (#286).
 *
 * Spec:
 *   - A child component that throws during render should cause ErrorBoundary
 *     to show a graceful fallback (not a blank tree / blank page).
 *   - The fallback must include a recover action ("Back to home" or reload).
 *   - When no child throws, it renders the children normally.
 *
 * WHY class component: React error boundaries must be class components —
 * this is the one sanctioned exception to the functional component norm.
 *
 * WHY suppress console.error: React always logs caught boundary errors to
 * console.error even when they are caught. We suppress during tests to keep
 * CI output clean; we do NOT suppress the actual thrown error.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ErrorBoundary } from "../components/ErrorBoundary";

// ── Helpers ───────────────────────────────────────────────────────────────────

/**
 * A component that throws once on render and then works normally.
 * Used to simulate a transient error.
 */
let shouldThrow = false;
function ThrowingChild({ message }: { message?: string }) {
  if (shouldThrow) {
    throw new Error(message ?? "Test explosion");
  }
  return <div data-testid="child-ok">All good</div>;
}

// ── Tests ─────────────────────────────────────────────────────────────────────

describe("ErrorBoundary", () => {
  // Suppress React's own console.error for boundary errors — they are expected here.
  let consoleErrorSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    shouldThrow = false;
    consoleErrorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
  });

  afterEach(() => {
    consoleErrorSpy.mockRestore();
  });

  it("renders children normally when no error is thrown", () => {
    render(
      <ErrorBoundary>
        <ThrowingChild />
      </ErrorBoundary>,
    );
    expect(screen.getByTestId("child-ok")).toBeInTheDocument();
    expect(screen.queryByTestId("error-boundary-fallback")).not.toBeInTheDocument();
  });

  it("renders the fallback when a child throws", () => {
    shouldThrow = true;
    render(
      <ErrorBoundary>
        <ThrowingChild />
      </ErrorBoundary>,
    );
    // The children must NOT be in the tree.
    expect(screen.queryByTestId("child-ok")).not.toBeInTheDocument();
    // The fallback must appear.
    expect(screen.getByTestId("error-boundary-fallback")).toBeInTheDocument();
  });

  it("fallback includes a meaningful error message", () => {
    shouldThrow = true;
    render(
      <ErrorBoundary>
        <ThrowingChild />
      </ErrorBoundary>,
    );
    // Must communicate that something went wrong (not a blank page).
    const fallback = screen.getByTestId("error-boundary-fallback");
    expect(fallback.textContent).toMatch(/something went wrong|unexpected error|error/i);
  });

  it("fallback includes a recover action (reload or back-to-home button)", () => {
    shouldThrow = true;
    render(
      <ErrorBoundary>
        <ThrowingChild />
      </ErrorBoundary>,
    );
    // Must have at least one interactive recover control.
    const fallback = screen.getByTestId("error-boundary-fallback");
    const buttons = fallback.querySelectorAll("button, a[href]");
    expect(buttons.length).toBeGreaterThanOrEqual(1);
  });

  it("the recover button reloads or navigates (smoke: clicking does not throw)", () => {
    shouldThrow = true;
    // Stub window.location.reload so it doesn't crash jsdom.
    const reloadSpy = vi.fn();
    Object.defineProperty(window, "location", {
      value: { ...window.location, reload: reloadSpy },
      writable: true,
    });

    render(
      <ErrorBoundary>
        <ThrowingChild />
      </ErrorBoundary>,
    );

    const fallback = screen.getByTestId("error-boundary-fallback");
    const button = fallback.querySelector("button");
    expect(button).not.toBeNull();
    // Clicking the button should not throw.
    fireEvent.click(button!);
  });

  it("a custom fallback prop overrides the default fallback", () => {
    shouldThrow = true;
    render(
      <ErrorBoundary fallback={<div data-testid="custom-fallback">Custom!</div>}>
        <ThrowingChild />
      </ErrorBoundary>,
    );
    expect(screen.getByTestId("custom-fallback")).toBeInTheDocument();
    expect(screen.queryByTestId("error-boundary-fallback")).not.toBeInTheDocument();
  });
});
