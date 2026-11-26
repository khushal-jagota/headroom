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
    window.kind === kind && windowCoversModel(window, modelId)
  )) ?? null;
}

/** Null asks for the account-wide window. A model id asks for a window that covers that model. */
export function windowCoversModel(window: BackendUsageWindow, modelId: string | null): boolean {
  if (modelId === null) return (window.model_id ?? null) === null;
  return window.model_ids.includes(modelId);
}

export function usageIsNearlySpent(percent: number): boolean {
  return percent >= USAGE_NEARLY_SPENT_PERCENT;
}

/** Allowance bars and rings share the same clamp and remaining semantics. */
export function remainingPercent(usedPercent: number): number {
  return 100 - Math.min(100, Math.max(0, usedPercent));
}
