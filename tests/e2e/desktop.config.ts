import { defineConfig } from '@playwright/test';
import base from './playwright.config.ts';

// The desktop app checks. They drive the built app in `desktop/build` and need a display:
// run them under `xvfb-run` on Linux.
export default defineConfig({
  ...base,
  testDir: 'desktop',
  testMatch: '**/*.spec.ts',
  testIgnore: [],
  // One Electron app at a time keeps the virtual display and its timing predictable.
  workers: 1,
});
