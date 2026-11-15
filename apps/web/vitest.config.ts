import { defineConfig } from "vitest/config";

/**
 * Unit tests for the client.
 *
 * jsdom, because the interesting behaviour here is what survives a bad response and what
 * the user sees while waiting — both of which need a DOM and React. End-to-end coverage of
 * the real journey lives in the backend suite (`tests/test_journey.py`) and, for the
 * browser, in a manual walkthrough documented in docs/DEMO.md.
 */
export default defineConfig({
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.{ts,tsx}"],
    restoreMocks: true,
  },
});
