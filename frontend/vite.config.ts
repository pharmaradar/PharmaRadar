import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { "@": path.resolve(__dirname, "./src") },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": "http://localhost:8009",
    },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
  },
  // VITE_API_URL is set in Railway env vars to https://your-backend.railway.app
  define: {
    __API_BASE__: JSON.stringify(process.env.VITE_API_URL || ""),
  },
});
