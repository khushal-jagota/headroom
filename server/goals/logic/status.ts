import type { Tone } from '../contracts.ts';
import { calendar_parts, with_calendar_iso } from '../calendar.ts';
import type { ChartBlock, MeasureValue } from '../../../web/src/lib/goals/contracts.ts';
import { calendar_date } from '../../measures/logic/dates.ts';
import { numeric_projection } from '../category_contracts.ts';
import type { ChartInput } from '../category_contracts.ts';
export function goal_status(completed: boolean, deadline: string | null, today: string): Tone {
  if (completed) return 'good';
  if (deadline === null || today < deadline) return 'neutral';
  return today === deadline ? 'attention' : 'bad';
}
/** Explicit fixed-offset clock text preserves Python TestClock's calendar. */
export function calendar_projection(now: Date, calendar_iso?: string): [string, number] {
  return calendar_parts(with_calendar_iso(now, calendar_iso));
}
export function quantitative_status(
  value: number | null,
  target: number | null,
  direction: string,
): Tone {
  if (value === null || target === null) return 'neutral';
  return (direction === 'atmost' ? value <= target : value >= target) ? 'good' : 'bad';
}
export function chart_target(input: ChartInput, reading_date: string): number | null {
  const chart = numeric_projection<ChartBlock>(input);
  const references = [...(chart.reference ?? [])].sort((a, b) =>
    a.date < b.date ? -1 : a.date > b.date ? 1 : 0,
  );
  if (!references.length) return chart.target ?? null;
  const exact = references.find((point) => point.date === reading_date);
  if (exact) return exact.value;
  const left = references.filter((point) => point.date < reading_date).at(-1);
  const right = references.find((point) => point.date > reading_date);
  if (!left || !right) return null;
  let elapsed: number;
  try {
    elapsed =
      (calendar_date(reading_date).getTime() - calendar_date(left.date).getTime()) / 86400000;
  } catch {
    return null;
  }
  const duration =
    (calendar_date(right.date).getTime() - calendar_date(left.date).getTime()) / 86400000;
  return left.value + ((right.value - left.value) * elapsed) / duration;
}
export function chart_summary(
  input: ChartInput,
  observations: (MeasureValue | [string, number | null])[],
): [number | null, number | null] {
  let chart = numeric_projection<ChartBlock>(input);
  const selected =
    chart.ranges?.find((group) => group.id === 'all') ??
    ('series' in chart ? undefined : chart.ranges?.[0]);
  const series = selected?.series ?? chart.series ?? [];
  if (selected && 'target' in selected) chart = { ...chart, target: selected.target };
  const points = new Map<string, Record<string, number | null>>();
  let categories = ['actual'];
  if (chart.measure_id) {
    const pairs = observations
      .map((item) =>
        Array.isArray(item) ? item : ([item.date, item.value] as [string, number | null]),
      )
      .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0));
    for (const [label, value] of pairs) points.set(label, { actual: value });
  } else {
    categories = series.map((item, index) => (index === 0 ? 'actual' : item.name));
    series.forEach((item, index) =>
      item.points.forEach((point) => {
        if (!points.has(point.label)) points.set(point.label, {});
        points.get(point.label)![categories[index]!] = point.value;
      }),
    );
  }
  if (!points.size) return [null, chart.target ?? null];
  for (const [label, values] of points)
    if (chart.reference?.length || 'target' in chart) values.target = chart_target(chart, label);
  const latest = [...points.values()].at(-1)!;
  if (chart.stacked || chart.kind === 'allocation') {
    const parts = categories.filter((key) => key !== 'target').map((key) => latest[key] ?? null);
    return [
      parts.length && !parts.includes(null)
        ? (parts as number[]).reduce((total, value) => total + value, 0)
        : null,
      latest.target ?? null,
    ];
  }
  if (chart.aggregate === 'sum') {
    const actuals = [...points.values()]
      .map((point) => point.actual)
      .filter((value): value is number => value !== null && value !== undefined);
    const targets = [...points.values()]
      .map((point) => point.target)
      .filter((value): value is number => value !== null && value !== undefined);
    return [
      actuals.length ? actuals.reduce((total, value) => total + value, 0) : null,
      targets.length ? targets.reduce((total, value) => total + value, 0) : null,
    ];
  }
  let value = latest.actual ?? null;
  if ((chart.kind === 'progress' || chart.kind === 'time-budget') && value !== null && value < 0)
    value = null;
  return [value, latest.target ?? null];
}
export function chart_status(
  chart: ChartInput | null,
  observations: (MeasureValue | [string, number | null])[],
): Tone {
  if (chart === null) return 'neutral';
  const [value, target] = chart_summary(chart, observations);
  const tone = quantitative_status(
    value,
    target,
    chart.direction ?? (chart.kind === 'time-budget' ? 'atmost' : 'atleast'),
  );
  return tone === 'bad' ? (chart.below_target_tone ?? 'bad') : tone;
}
