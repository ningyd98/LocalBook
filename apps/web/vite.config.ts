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

/**
 * Hosts the dev server accepts when it is reached through a reverse proxy
 * (nginx -> frpc -> 127.0.0.1:5173). Vite 5.4 blocks unknown Host headers
 * with "Blocked request" unless they are listed here; `true` allows any host.
 * The public entry point is protected by HTTP Basic Auth at the edge.
 */
const allowedHosts: true | string[] =
  process.env.VITE_ALLOWED_HOSTS === "off" ? [] : true;

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    allowedHosts,
    proxy: {
      "/api": {
        target: proxyTarget,
        changeOrigin: false,
        // Append X-Forwarded-For with the real client address. The backend's
        // local-only settings guard reads the right-most hop, so it can tell a
        // loopback caller (this dev server behind nginx/frp) from a public one.
        xfwd: true,
      },
    },
  },
  test: {
    environment: "jsdom",
    include: ["../../tests/frontend/**/*.test.{ts,tsx}"],
    setupFiles: ["../../tests/frontend/setup.ts"],
    // `LOCALNOTE_E2E_BASE_URL` (see tests/frontend/.env.test) turns the opt-in
    // real-backend integration test on; without it those specs skip.
    env: { LOCALNOTE_E2E_BASE_URL: process.env.LOCALNOTE_E2E_BASE_URL ?? "" },
    css: false,
  },
});
