import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The API base path. In the container, nginx proxies `/api/` to the api
// service; in local `vite dev`, the proxy below forwards `/api` to the API on
// localhost so the app talks to the same relative path in both environments.
const API_TARGET = process.env.VITE_API_PROXY_TARGET ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: API_TARGET,
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
});
