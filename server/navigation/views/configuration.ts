/** Python's bounded view budgets, shared by projection and read paths. */
export const VIEW_CONFIG = Object.freeze({
  maximum_records: 512,
  maximum_bytes: 768 * 1024,
  maximum_windows: 160,
  maximum_window_rows: 60,
  maximum_requested_rows: 256,
  inline_recap_bytes: 2048,
  content_chunk_bytes: 64 * 1024,
  retained_revisions: 10000,
  locales: ['en-US', 'de-DE'] as readonly string[],
});
