import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Databricks Apps serves this build as static files from the FastAPI process
// (see server/main.py); locally the dev server proxies API calls to it
// instead so `npm run dev` needs no separate CORS setup.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "dist",
  },
  server: {
    port: 5173,
    proxy: {
      "/v1": "http://127.0.0.1:8000",
      "/version": "http://127.0.0.1:8000",
    },
  },
});
