import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

/**
 * LocalNote web (Phase 0) Vite + Vitest config — the single frontend entry.
 *
 * Dev: `vite` binds 127.0.0.1:5173 and proxies `/api` to the FastAPI backend
 * (default http://127.0.0.1:3780, override with VITE_API_PROXY_TARGET).
 * scripts/dev.sh exports VITE_API_PROXY_TARGET before launching Vite.
 *
 * Vitest runs the frontend tests living in ../../tests/frontend (plan layout)
 * with jsdom; fetch is mocked per test — no real backend or oMLX.
 */
const proxyTarget = process.env.VITE_API_PROXY_TARGET ?? "http://127.0.0.1:3780";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: {
      "/api": {
        target: proxyTarget,
        changeOrigin: false,
      },
    },
  },
  test: {
    environment: "jsdom",
    include: ["../../tests/frontend/**/*.test.{ts,tsx}"],
    setupFiles: ["../../tests/frontend/setup.ts"],
    css: false,
  },
});
