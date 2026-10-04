import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  // Never inline assets as data: URIs; the app's CSP allows fonts only from 'self'.
  build: { assetsInlineLimit: 0 },
  server: { proxy: { "/api": "http://127.0.0.1:8765" } },
  test: { environment: "jsdom" },
});
