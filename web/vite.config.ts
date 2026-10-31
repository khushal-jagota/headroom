import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { usageAliases } from "./usage-aliases.mjs";
import { svelte } from "@sveltejs/vite-plugin-svelte";
import { defineConfig } from "vite";
import { markdownWorkerPlugins } from "./build/markdown-worker.mjs";

const webRoot = dirname(fileURLToPath(import.meta.url));

const backend = process.env.PLAN_WEB_BACKEND || "http://127.0.0.1:8787";

export default defineConfig({
  base: "/_app/",
  resolve: { alias: usageAliases },
  plugins: [svelte()],
  worker: { plugins: markdownWorkerPlugins },
  build: {
    outDir: "dist",
    emptyOutDir: true,
    rollupOptions: {
      input: {
        main: resolve(webRoot, "index.html"),
        pageKit: resolve(webRoot, "page-kit.html")
      }
    }
  },
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: false,
    fs: { allow: [resolve(webRoot, "..")] },
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
