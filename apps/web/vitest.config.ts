import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// Test configuration is kept separate from vite.config.ts so the production
// build config stays type-checked by tsc without pulling in Vitest's Vite
// types (which can differ from the top-level Vite version).
export default defineConfig({
  plugins: [react()],
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./src/setupTests.ts"],
    css: false,
  },
});
