/**
 * Shapes local daily totals into the per-day provider stacks both chart
 * implementations (Swift Charts on iOS, plain views elsewhere) render.
 *
 * @module usageChartData
 */
import type { UsageProviderKind } from "@t3tools/contracts";
import type { DailyTotals } from "./usageTotals";

export type UsageChartMetric = "cost" | "tokens";

export interface UsageChartDay {
  readonly day: string;
  /** In the given stacking order, i.e. bottom of the stack first. */
  readonly values: readonly { readonly provider: UsageProviderKind; readonly value: number }[];
  readonly total: number;
}

/** One entry per day in the window, zero-filled where nothing happened. A source the
 *  order does not name stacks above the named ones, so every total stays drawn. */
export function buildChartDays(
  days: readonly string[],
  daily: readonly DailyTotals[],
  metric: UsageChartMetric,
  order: readonly UsageProviderKind[],
): readonly UsageChartDay[] {
  const byDay = new Map(daily.map((totals) => [totals.day, totals]));
  const unnamed = [...new Set(daily.flatMap((totals) => [...totals.byProvider.keys()]))]
    .filter((provider) => !order.includes(provider));
  const stacking = [...order, ...unnamed];
  return days.map((day) => {
    const totals = byDay.get(day);
    const values = stacking.map((provider) => {
      const entry = totals?.byProvider.get(provider);
      const value = entry === undefined ? 0 : metric === "cost" ? entry.costUsd : entry.totalTokens;
      return { provider, value };
    });
    return {
      day,
      values,
      total: values.reduce((sum, entry) => sum + entry.value, 0),
    };
  });
}
