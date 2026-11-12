/** Existing Python limits and fixed source protocol values. */
export const LITELLM_RATES_URL =
  'https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json';
export const RATES_TTL_MS = 24 * 60 * 60 * 1000;
export const RATES_REFRESH_FLOOR_MS = 60 * 1000;
export const MTIME_SLACK_MS = 36 * 60 * 60 * 1000;
export const MAX_HOURLY_WINDOW_MS = 24 * 60 * 60 * 1000;
export const CACHE_RETENTION_DAYS = 90;
export const RATES_TIMEOUT_MS = 10 * 1000;
export const FORK_COPY_MAX_GAP_MS = 1000;
export const TRANSCRIPT_CHUNK_BYTES = 64 * 1024;
