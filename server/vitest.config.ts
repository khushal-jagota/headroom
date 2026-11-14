import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    // Node 24 WASM wrapper lifetime race: https://github.com/nodejs/node/issues/66366
    execArgv: process.versions.node.startsWith('24.') ? ['--no-wasm-code-gc'] : [],
    globalSetup: ['./tests/global_setup.ts'],
    setupFiles: ['./tests/setup.ts'],
  },
});
