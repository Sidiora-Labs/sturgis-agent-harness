import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";
import { availableParallelism } from "node:os";

export default defineConfig({
  root: fileURLToPath(new URL("..", import.meta.url)),
  plugins: [react()],
  test: {
    environment: "jsdom",
    // Leave CPU headroom for jsdom and V8 coverage on shared hosts.
    maxWorkers: Math.min(2, availableParallelism()),
    testTimeout: 10000,
    include: ["frontend/tests/**/*.test.{ts,tsx}"],
    setupFiles: ["frontend/tests/setup.ts"],
    clearMocks: true,
    restoreMocks: true,
    coverage: {
      provider: "v8",
      include: ["frontend/src/**/*.{ts,tsx,js,jsx}"],
      exclude: [],
      reporter: ["json", "text"],
      reportsDirectory: ".build/web-vitest",
      clean: true,
      reportOnFailure: false,
    },
  },
});
