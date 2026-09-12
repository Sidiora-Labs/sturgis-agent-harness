import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";

export default defineConfig({
  root: fileURLToPath(new URL("..", import.meta.url)),
  plugins: [react()],
  test: {
    environment: "jsdom",
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
