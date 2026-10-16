/** Mirrors conversation/usage/contracts.py. Reads never scan native source files. */
import { fetchJson } from "../api";

export type UsageProvider = "codex" | "claude";
export type UsageDays = 1 | 7 | 30 | 90;
export type UsageSourceState = "ready" | "partial" | "unavailable" | "failed";
export type UsageMetrics = {
  input_tokens: number | null;
  uncached_input_tokens: number | null;
  cached_input_tokens: number | null;
  cache_creation_tokens: number | null;
  output_tokens: number | null;
  reasoning_tokens: number | null;
  total_tokens: number | null;
  reported_cost_usd: number | null;
  record_count: number;
  session_count: number | null;
  unknown_records: number;
  unknown_cost_records: number;
};
export type ProviderUsageMetrics = UsageMetrics & { provider: UsageProvider };
export type ModelUsageMetrics = ProviderUsageMetrics & { model: string | null };
export type PeriodUsageMetrics = UsageMetrics & {
  start: string;
  end: string;
  label: string;
  providers: ProviderUsageMetrics[];
};
export type UsageSourceSummary = {
  provider: UsageProvider;
  state: UsageSourceState;
  observed_at: string | null;
  files_scanned: number;
  record_count: number;
  issues: string[];
  duplicates: number;
};
export type UsageSummary = {
  scope: "local_cli";
  days: UsageDays;
  timezone: string;
  since: string;
  until: string;
  observed_at: string | null;
  sources: UsageSourceSummary[];
  totals: UsageMetrics;
  providers: ProviderUsageMetrics[];
  models: ModelUsageMetrics[];
  periods: PeriodUsageMetrics[];
};

/** The only browser operation that ingests native CLI records. Explicit action only. */
export function refreshRecordedUsage(): Promise<{ sources: UsageSourceSummary[] }> {
  return fetchJson("/api/conversation/usage/refresh", { method: "POST" });
}
