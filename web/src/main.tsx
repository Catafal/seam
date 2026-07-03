import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "./App";
import { ErrorBoundary } from "./components/ErrorBoundary";
import "./index.css";

// Single TanStack Query client shared across the app.
// staleTime=60s: symbol data doesn't change while the explorer is open.
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 60_000,
      retry: 1,
    },
  },
});

const root = document.getElementById("root");
if (!root) throw new Error("Root element not found");

// WHY ErrorBoundary wraps the whole tree:
//   Any unhandled render error would leave the user on a blank black screen
//   with no recovery path. The ErrorBoundary guarantees a graceful, recoverable
//   fallback for any render crash anywhere in the app (#286).
createRoot(root).render(
  <StrictMode>
    <ErrorBoundary>
      <QueryClientProvider client={queryClient}>
        <App />
      </QueryClientProvider>
    </ErrorBoundary>
  </StrictMode>,
);
