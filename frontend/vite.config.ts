/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In development, `python app.py --dev` runs the API on this port and Vite
// forwards /api (including the live WebSocket) to it.
const API = "http://127.0.0.1:8765";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: { "/api": { target: API, changeOrigin: true, ws: true } },
  },
  test: {
    environment: "jsdom",
  },
});
