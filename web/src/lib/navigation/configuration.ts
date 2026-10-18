/** Browser synchronization limits; independent of production consumer behavior. */
export const navigationConfiguration = {
  maximumPendingBatches: 64,
  reconnectDelayMs: 500,
  visibilityTimeoutMs: 10_000,
} as const;
