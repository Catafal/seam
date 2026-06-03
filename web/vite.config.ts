/// <reference types="vitest" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Build output goes to seam/_web/ so the Python wheel can include it as a
// package artifact (see pyproject.toml [tool.hatch.build.targets.wheel] artifacts).
// base "./" ensures all asset imports in index.html use relative paths so the
// SPA works when served from any sub-path by FastAPI's StaticFiles mount.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "../seam/_web",
    emptyOutDir: true,
  },
  base: "./",
  server: {
    // Dev proxy: all /api/* requests are forwarded to the running seam serve
    // process on port 7420 so `npm run dev` + `seam serve` work together.
    proxy: {
      "/api": {
        target: "http://127.0.0.1:7420",
        changeOrigin: false,
      },
    },
  },
  test: {
    // jsdom gives us a browser-like DOM environment for React component tests.
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/__tests__/setup.ts"],
  },
});
