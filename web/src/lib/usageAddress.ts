export type UsageTab = "tokens" | "cost" | "limits";
export type UsageSelection = { tab: UsageTab };

/** Parse the suffix after #/backends. Its old Config tab now lives in Settings. */
export function parseUsageSelection(segments: readonly string[]): UsageSelection | null {
  const tab = segments[0] || "limits";
  if (["tokens", "cost", "limits"].includes(tab) && segments.length <= 1) return { tab: tab as UsageTab };
  return null;
}
