import type {
  PeriodUsageMetrics, UsageMetrics, UsageProvider, UsageSummary
} from "../../lib/conversation/recordedUsageWire";

export type UsageMetric = "tokens" | "cost" | "limits";
export const USAGE_PERIODS = [
  { days: 1, label: "24h" }, { days: 7, label: "7d" },
  { days: 30, label: "30d" }, { days: 90, label: "90d" }
] as const;
export const providerLabel = (provider: string): string =>
  provider ? provider[0].toUpperCase() + provider.slice(1) : "Unknown";
export function formatTokens(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return "—";
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(2)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(value >= 100_000 ? 0 : 1)}K`;
  return value.toLocaleString();
}
export function formatCost(value: number | null): string {
  return value === null || !Number.isFinite(value) ? "—" : new Intl.NumberFormat(undefined, {
    style: "currency", currency: "USD", maximumFractionDigits: 2
  }).format(value);
}
export const metricValue = (metrics: UsageMetrics, metric: "tokens" | "cost"): number | null =>
  metric === "tokens" ? metrics.total_tokens : metrics.reported_cost_usd;

const nullableKeys = [
  "input_tokens", "uncached_input_tokens", "cached_input_tokens", "cache_creation_tokens",
  "output_tokens", "reasoning_tokens", "total_tokens", "reported_cost_usd", "session_count"
] as const;
/** Missing contributors stay unknown. Subset categories never join inclusive totals. */
export function combineMetrics(rows: readonly UsageMetrics[]): UsageMetrics {
  const result: UsageMetrics = {
    input_tokens: null, uncached_input_tokens: null, cached_input_tokens: null,
    cache_creation_tokens: null, output_tokens: null, reasoning_tokens: null,
    total_tokens: null, reported_cost_usd: null, session_count: null,
    record_count: 0, unknown_records: 0, unknown_cost_records: 0
  };
  for (const key of nullableKeys) {
    if (rows.length && rows.every(row => row[key] !== null)) {
      result[key] = rows.reduce((sum, row) => sum + (row[key] as number), 0);
    }
  }
  for (const row of rows) {
    result.record_count += row.record_count;
    result.unknown_records += row.unknown_records;
    result.unknown_cost_records += row.unknown_cost_records;
  }
  return result;
}
export function selectedTotals(summary: UsageSummary, providers: readonly UsageProvider[]): UsageMetrics {
  return summary.providers.every(row => providers.includes(row.provider))
    ? summary.totals
    : combineMetrics(summary.providers.filter(row => providers.includes(row.provider)));
}
export type ChartPoint = { period: PeriodUsageMetrics; value: number; index: number };
/** A missing provider, null metric, or omitted interval always breaks the line. */
export function chartSegments(
  periods: readonly PeriodUsageMetrics[], provider: UsageProvider, metric: "tokens" | "cost"
): ChartPoint[][] {
  const segments: ChartPoint[][] = [];
  let current: ChartPoint[] = [];
  periods.forEach((period, index) => {
    const entry = period.providers.find(row => row.provider === provider);
    const value = entry ? metricValue(entry, metric) : null;
    if (value === null || !Number.isFinite(value)) {
      current = [];
      return;
    }
    const previous = current.at(-1);
    if (!previous || Date.parse(previous.period.end) !== Date.parse(period.start)) {
      current = [];
      segments.push(current);
    }
    current.push({ period, value, index });
  });
  return segments;
}
/** Inspect the nearest observed period from a tap or pointer coordinate. */
export function chartPointerIndex(
  periods: readonly Pick<PeriodUsageMetrics, "start">[], clientX: number,
  bounds: { left: number; width: number }
): number | null {
  if (!periods.length || bounds.width <= 0) return null;
  const first = Date.parse(periods[0].start);
  const last = Date.parse(periods[periods.length - 1].start);
  const fraction = Math.max(0, Math.min(1, (clientX - bounds.left) / bounds.width));
  const moment = first + fraction * (last - first);
  return periods.reduce((closest, period, index) =>
    Math.abs(Date.parse(period.start) - moment) < Math.abs(Date.parse(periods[closest].start) - moment)
      ? index : closest, 0);
}
export function tokenSegments(metrics: UsageMetrics): Array<{ label: string; value: number; color: string }> {
  const categories = [
    { label: "Input", value: metrics.uncached_input_tokens, color: "var(--text-default)" },
    { label: "Cached input", value: metrics.cached_input_tokens, color: "var(--text-faintest)" },
    { label: "Cache write", value: metrics.cache_creation_tokens, color: "var(--text-muted)" },
    { label: "Output", value: metrics.output_tokens, color: "var(--text-strong)" }
  ];
  if (metrics.total_tokens === null || categories.some(row => row.value === null)) return [];
  const known = categories as Array<{ label: string; value: number; color: string }>;
  return known.reduce((sum, row) => sum + row.value, 0) === metrics.total_tokens ? known : [];
}
export function timestampLabel(value: string | null, timezone?: string): string {
  if (!value || !Number.isFinite(Date.parse(value))) return "Unavailable";
  return new Intl.DateTimeFormat(undefined, {
    month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", timeZone: timezone
  }).format(new Date(value));
}
