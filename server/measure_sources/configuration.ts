import type { RuntimeLimits } from './contracts.ts';
export const DEFAULT_LIMITS: RuntimeLimits = {
  wall_seconds: 30,
  output_bytes: 8 * 1024 * 1024,
  response_bytes: 64 * 1024 * 1024,
  heap_megabytes: 256,
};
export const DEFAULT_PAGE_LIMIT = 30;
export const MAX_PAGE_LIMIT = 100;
export const LATEST_VALUES_PREVIEW = 5;
export const MAX_CONCURRENT_RUNS = 2;
