import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    // Outside CI, two workers: an uncapped run on the shared VPS starts hundreds of processes.
    maxWorkers: process.env.CI ? undefined : 2,
    globalSetup: ['./tests/global_setup.ts'],
    setupFiles: ['./tests/setup.ts'],
  },
});
