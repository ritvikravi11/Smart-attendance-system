import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // DEV PROXY: the browser calls /api/... on :5173 and Vite forwards it to
    // the FastAPI server on :8000. Two benefits:
    //   1. No CORS preflight in development -- same origin from the browser's view.
    //   2. The API base URL is "/api/v1" in both dev and production, so no
    //      environment-specific URL juggling in the client code.
    proxy: {
      "/api": { target: "http://localhost:8000", changeOrigin: true },
    },
  },
  build: { outDir: "dist", sourcemap: true },
});
