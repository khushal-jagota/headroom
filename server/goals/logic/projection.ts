import type { Tone } from '../contracts.ts';
import type {
  ScalarChartPoint,
  ScalarChartProjection,
  MeasureValue,
  ExpressionValue,
} from '../../../web/src/lib/goals/contracts.ts';
import { calendar_date, offset_date, invalid } from '../../measures/logic/dates.ts';
import type { ChartInput } from '../category_contracts.ts';
import { DEFAULT_PROJECTION_DATES, MAX_SCALAR_PROJECTION_DATES } from '../configuration.ts';
export function projection_bounds(
  today: string,
  earliest: string | null,
  deadline: string | null,
  first_date: string | null,
  last_date: string | null,
): [string, string, number] {
  const ordinal =
    Math.floor(
      (calendar_date(today).getTime() - calendar_date('0001-01-01').getTime()) / 86400000,
    ) + 1;
  const start = offset_date(today, -Math.min(ordinal - 1, DEFAULT_PROJECTION_DATES - 1));
  const first = first_date ?? [earliest ?? start, start].sort()[0]!;
  const last = last_date ?? [today, deadline ?? today].sort().at(-1)!;
  const length = (calendar_date(last).getTime() - calendar_date(first).getTime()) / 86400000 + 1;
  if (length < 1) throw invalid('goal date range is reversed');
  if (length > MAX_SCALAR_PROJECTION_DATES)
    throw invalid(
      `goal projection exceeds ${MAX_SCALAR_PROJECTION_DATES} dates per chart; request a narrower date range`,
    );
  return [first, last, length];
}
export function projection_point(
  day: string,
  actual: MeasureValue,
  expected: ExpressionValue,
  direction: string,
  break_before: boolean,
): ScalarChartPoint {
  let difference: number | null = null;
  let meets_expectation: boolean | null = null;
  const left = actual.value,
    right = expected.value;
  if (left !== null && right !== null && Number.isFinite(left) && Number.isFinite(right)) {
    const delta = left - right;
    difference = Number.isFinite(delta) ? delta : null;
    meets_expectation = direction === 'atmost' ? left <= right : left >= right;
  }
  return { date: day, actual, expected, difference, meets_expectation, break_before };
}
export function scalar_projection(
  first: string,
  last: string,
  today: string,
  points: ScalarChartPoint[],
): ScalarChartProjection {
  return {
    kind: 'scalar',
    first_date: first,
    last_date: last,
    today,
    expected_source: 'expectation',
    points,
    summary_date: first <= today ? [last, today].sort()[0]! : null,
  };
}
export function projection_status(chart: ChartInput, projection: ScalarChartProjection): Tone {
  const point = projection.points.find((point) => point.date === projection.summary_date);
  if (!point || point.meets_expectation === null) return 'neutral';
  return point.meets_expectation ? 'good' : (chart.below_target_tone ?? 'bad');
}
