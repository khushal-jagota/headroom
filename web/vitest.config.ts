import { svelte } from "@sveltejs/vite-plugin-svelte";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [svelte()],
  test: {
    environment: "node",
    include: ["tests/**/*.test.ts"],
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
      include: ["tests/**/*.test.ts"],
      tsconfig: "./tests/tsconfig.json"
    }
  }
});
