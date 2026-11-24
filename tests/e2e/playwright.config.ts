import { join } from 'node:path';
import { defineConfig } from '@playwright/test';

// The browser job sets BROWSER_EVIDENCE_DIR. Local runs keep evidence beside the tests.
const evidence = process.env.BROWSER_EVIDENCE_DIR;

export default defineConfig({
  testDir: '.',
  testMatch: '**/*.spec.ts',
  // desktop.config.ts runs the desktop app checks.
  testIgnore: 'desktop/**',
  // Every test starts its own server, so tests share nothing but the built web app.
  fullyParallel: true,
  workers: 2,
  retries: 0,
  // Each server boots inside its test, so the limit covers boot as well as the checks.
  timeout: 180_000,
  // The Python Playwright defaults the gates were written against. Explicit waits pass
  // WAIT_MS themselves.
  expect: { timeout: 5_000 },
  outputDir: process.env.PANELS_E2E_FAILURE_DIR ?? 'test-results',
  reporter: evidence
    ? [['list'], ['junit', { outputFile: join(evidence, 'results.xml') }]]
    : [['list']],
  use: { browserName: 'chromium', headless: true, actionTimeout: 30_000, navigationTimeout: 30_000 },
});
