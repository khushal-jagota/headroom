import type { BackendUsageWindow } from "./wire";

/** The single product threshold where an allowance reading becomes a warning. */
export const USAGE_NEARLY_SPENT_PERCENT = 90;

export type UsageWindowKind = BackendUsageWindow["kind"];

export function usageWindow(
  windows: readonly BackendUsageWindow[],
  kind: UsageWindowKind,
  modelId: string | null = null
): BackendUsageWindow | null {
  return windows.find((window) => (
    window.kind === kind && (window.model_id ?? null) === modelId
  )) ?? null;
}

export function usageIsNearlySpent(percent: number): boolean {
  return percent >= USAGE_NEARLY_SPENT_PERCENT;
}
