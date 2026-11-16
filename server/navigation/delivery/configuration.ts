/** The same independent delivery budgets as the Python navigation runtime. */
export const DELIVERY = {
  pollMilliseconds: 1000,
  heartbeatMilliseconds: 15_000,
  sendTimeoutMilliseconds: 10_000,
  retentionMilliseconds: 30_000,
  retainedBatchCount: 10_000,
  retainedBytes: 64 * 1024 * 1024,
  contentChunkBytes: 64 * 1024,
} as const;
