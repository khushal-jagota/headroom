import { usageAliases } from "./usage-aliases.mjs";
import { svelte } from "@sveltejs/vite-plugin-svelte";
import { defineConfig } from "vitest/config";

export default defineConfig({
  resolve: { alias: usageAliases },
  plugins: [svelte()],
  test: {
    // Outside CI, two workers: an uncapped run on the shared VPS starts hundreds of processes.
    maxWorkers: process.env.CI ? undefined : 2,
    environment: "node",
    include: ["tests/**/*.test.ts", "src/lib/usage/*.test.ts"],
    clearMocks: true,
    restoreMocks: true,
    unstubGlobals: true,
    server: {
      deps: {
        inline: [/@tanstack\/svelte-query/]
      }
    },
    typecheck: {
      enabled: true,
      include: ["tests/**/*.test.ts", "src/lib/usage/*.test.ts"],
      tsconfig: "./tests/tsconfig.json"
    }
  }
});
