import { svelte } from "@sveltejs/vite-plugin-svelte";
import { defineConfig } from "vite";

const backend = process.env.PLAN_WEB_BACKEND || "http://127.0.0.1:8787";

export default defineConfig({
  base: "/_app/",
  plugins: [svelte()],
  build: {
    outDir: "dist",
    emptyOutDir: true
  },
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: false,
    proxy: {
      "/api": {
        target: backend,
        changeOrigin: true,
        ws: true
      },
      "/assets": {
        target: backend,
        changeOrigin: true
      },
      "/static": {
        target: backend,
        changeOrigin: true
      }
    }
  },
  preview: {
    host: "127.0.0.1",
    port: 4173,
    strictPort: false,
    proxy: {
      "/api": {
        target: backend,
        changeOrigin: true,
        ws: true
      },
      "/assets": {
        target: backend,
        changeOrigin: true
      },
      "/static": {
        target: backend,
        changeOrigin: true
      }
    }
  }
});
