import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The API base URL is injected at build time from VITE_API_BASE_URL. Everything
// Vite exposes with the VITE_ prefix is PUBLIC: never put a secret here.
export default defineConfig({
  plugins: [react()],
  // Share one environment file with the backend, at the repository root.
  envDir: "../..",
  server: {
    port: 5173,
    host: true,
  },
  build: {
    outDir: "dist",
    sourcemap: false,
  },
});
