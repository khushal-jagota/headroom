/** Thin HTTP transport for the copied T3 usage contracts. */
import { fetchJson } from "../api";
import type { ServerProviderUsageLimits, UsageSummaryInput, UsageSummary, UsagePricing } from "../usage/contracts";
export type { UsageSummaryInput, UsageSummary, UsageProviderKind as UsageProvider } from "../usage/contracts";
export type UsageDays = 1 | 7 | 30 | 90;
export type LocalUsageLimits = Readonly<Record<string, ServerProviderUsageLimits>>;

export function usageSummaryPath(input: UsageSummaryInput): string {
  const query = new URLSearchParams({ sinceDay: input.sinceDay, untilDay: input.untilDay, timeZone: input.timeZone });
  if (input.resolution !== undefined) query.set("resolution", input.resolution);
  if (input.sinceTime !== undefined) query.set("sinceTime", input.sinceTime);
  if (input.untilTime !== undefined) query.set("untilTime", input.untilTime);
  return `/api/conversation/usage/summary?${query}`;
}
export function readUsageSummary(input: UsageSummaryInput): Promise<UsageSummary> {
  return fetchJson(usageSummaryPath(input));
}
export function readUsageLimits(): Promise<LocalUsageLimits> {
  return fetchJson("/api/conversation/usage/limits");
}

/** Pricing only. This does not refresh a native provider or account. */
export function refreshUsageRates(): Promise<UsagePricing> {
  return fetchJson("/api/conversation/usage/rates/refresh", { method: "POST" });
}
