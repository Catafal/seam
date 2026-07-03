/**
 * ErrorBoundary — app-level React error boundary (#286).
 *
 * WHY a class component:
 *   React error boundaries MUST be class components. This is the single
 *   sanctioned exception to the project's functional-component norm —
 *   the `getDerivedStateFromError` + `componentDidCatch` lifecycle pair
 *   cannot be expressed with hooks.
 *
 * WHY two layers in App:
 *   1. App-level boundary (in main.tsx): catches any render error anywhere,
 *      guarantees no blank-page outcome for the user.
 *   2. Graph-surface boundary (inside GraphCanvas): isolates graph crashes so
 *      the header and StatusStrip stay alive even when the canvas throws.
 *
 * Usage:
 *   <ErrorBoundary>
 *     <App />
 *   </ErrorBoundary>
 *
 *   // Custom fallback:
 *   <ErrorBoundary fallback={<MyFallback />}>
 *     <GraphCanvas ... />
 *   </ErrorBoundary>
 */

import { Component } from "react";
import type { ReactNode, ErrorInfo } from "react";
import { AlertTriangle } from "lucide-react";

interface ErrorBoundaryProps {
  children: ReactNode;
  /** Optional custom fallback UI — overrides the default zinc-themed panel. */
  fallback?: ReactNode;
}

interface ErrorBoundaryState {
  hasError: boolean;
  error: Error | null;
}

export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  constructor(props: ErrorBoundaryProps) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  /**
   * Derived from error so the NEXT render shows the fallback immediately.
   * We capture the error object so we can display it in development.
   */
  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { hasError: true, error };
  }

  /**
   * Called after an error is caught. Log to console for debugging.
   * WHY not telemetry: Seam is local-first; no external error reporting.
   */
  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error("[Seam ErrorBoundary] Caught render error:", error, info.componentStack);
  }

  handleReload = (): void => {
    window.location.reload();
  };

  handleReset = (): void => {
    this.setState({ hasError: false, error: null });
  };

  render(): ReactNode {
    if (!this.state.hasError) {
      return this.props.children;
    }

    // Custom fallback overrides the default panel.
    if (this.props.fallback) {
      return this.props.fallback;
    }

    return (
      <div
        data-testid="error-boundary-fallback"
        className="flex flex-col items-center justify-center w-full h-full gap-6 p-8 bg-zinc-950 text-center"
      >
        {/* Icon */}
        <div className="p-4 rounded-full bg-zinc-800 border border-zinc-700">
          <AlertTriangle className="w-8 h-8 text-amber-400" aria-hidden="true" />
        </div>

        {/* Message */}
        <div className="flex flex-col items-center gap-2 max-w-sm">
          <h2 className="text-base font-semibold text-zinc-100">
            Something went wrong
          </h2>
          <p className="text-sm text-zinc-500 leading-relaxed">
            An unexpected error occurred in the Explorer. Your index data is safe —
            this is a display error only.
          </p>
          {/* Show the error message in development for debugging. */}
          {this.state.error && (
            <p className="text-xs text-zinc-600 font-mono mt-1 max-w-xs truncate" title={this.state.error.message}>
              {this.state.error.message}
            </p>
          )}
        </div>

        {/* Recovery actions */}
        <div className="flex gap-3">
          <button
            onClick={this.handleReset}
            className="px-4 py-2 text-xs font-semibold rounded-md
                       bg-sky-600 hover:bg-sky-500 text-white
                       transition-colors"
            aria-label="Try again"
          >
            Try again
          </button>
          <button
            onClick={this.handleReload}
            className="px-4 py-2 text-xs font-semibold rounded-md
                       bg-zinc-800 hover:bg-zinc-700 text-zinc-200
                       border border-zinc-600 transition-colors"
            aria-label="Reload the page"
          >
            Reload page
          </button>
        </div>
      </div>
    );
  }
}
