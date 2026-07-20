import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// The workbench talks to the engine's session backend (FastAPI/uvicorn, default :8099)
// through a same-origin `/api` proxy — so EventSource(SSE) and fetch never cross an origin
// and CORS/credentials edge-cases never bite. Override the target with VITE_API_TARGET.
const API_TARGET = process.env.VITE_API_TARGET ?? "http://localhost:8099";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": {
        target: API_TARGET,
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
        // keep SSE flowing: don't let the proxy buffer the event stream
        configure: (proxy) => {
          proxy.on("proxyReq", (proxyReq) => proxyReq.setHeader("Accept-Encoding", "identity"));
        },
      },
    },
  },
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
