/** Compose complete retained inputs inside the caller's read snapshot. */
import type { ReadSql } from '../core/database/contracts.ts';
import * as datasets from '../datasets/data/index.ts';
import type { Dataset } from '../datasets/contracts.ts';
import type {
  CategoryChartProjection,
  SourceHealthSummary,
  MeasureValue,
} from '../../web/src/lib/goals/contracts.ts';
import type { Definitions, MeasureDate } from '../measures/contracts.ts';
import {
  earliest_retained_date,
  read_definitions,
  read_recorded_dates,
  read_health,
} from '../measures/data.ts';
import { calendar_date, date_range, local_date, invalid } from '../measures/logic/dates.ts';
import type { ChartBlock, ReadingSubject } from './contracts.ts';
import { charts } from './logic/documents.ts';
import {
  category_bounds,
  dataset_chart_projection,
  scalar_inputs_chart_projection,
} from './logic/categories.ts';
import { check_goal_demand_count, goal_actual_value } from './logic/reading_assembly.ts';
import { read_goal_evaluation } from './reading_assembly.ts';
import { MAX_PROJECTED_CHART_DATES, MAX_DEMANDED_MEASURE_DATES } from './configuration.ts';
export interface ReadOptions {
  first_date?: string | null;
  last_date?: string | null;
}
type Plan = [ChartBlock, string | null, string, string];
const length = (first: string, last: string): number =>
  (calendar_date(last).getTime() - calendar_date(first).getTime()) / 86400000 + 1;
function plans(
  sql: ReadSql,
  goal: ReadingSubject,
  now: Date,
  options: ReadOptions,
): [Map<string, Dataset>, Definitions, Plan[]] {
  const bound = charts(goal.primary_chart, goal.page).filter((chart) => 'binding' in chart);
  const dataset_ids = new Set<string>(),
    measure_ids = new Set<string>();
  for (const chart of bound) {
    const binding = chart.binding!;
    if (binding.kind === 'dataset') dataset_ids.add(binding.dataset_id);
    else for (const item of binding.inputs) measure_ids.add(item.measure_id);
  }
  const dataset_definitions = new Map(
    [...dataset_ids].sort().map((id) => [id, datasets.read_dataset(sql, id)]),
  );
  const definitions = read_definitions(sql, [...measure_ids].sort());
  const result: Plan[] = [];
  let projected_dates = 0;
  for (const chart of bound) {
    const binding = chart.binding!;
    let zone: string, earliest: string | null;
    if (binding.kind === 'dataset') {
      const definition = dataset_definitions.get(binding.dataset_id)!;
      zone = definition.timezone;
      earliest = datasets.earliest_retained_date(sql, definition.id);
    } else {
      zone = definitions.get(binding.inputs[0]!.measure_id)!.timezone;
      const retained = binding.inputs
        .map((item) => earliest_retained_date(sql, item.measure_id))
        .filter((day): day is string => day !== null);
      earliest = retained.sort()[0] ?? null;
    }
    const today = local_date(Math.floor(now.getTime() / 1000), zone),
      bounds = category_bounds(
        today,
        earliest,
        options.first_date ?? null,
        options.last_date ?? null,
      );
    const first = bounds.map((item) => item[1]).sort()[0]!,
      last = bounds
        .map((item) => item[2])
        .sort()
        .at(-1)!;
    projected_dates += length(first, last);
    if (projected_dates > MAX_PROJECTED_CHART_DATES)
      throw invalid(
        `goal projections exceed ${MAX_PROJECTED_CHART_DATES} chart-date points; request a narrower date range`,
      );
    result.push([chart, earliest, first, last]);
  }
  return [dataset_definitions, definitions, result];
}
function measure_ranges(plans: Plan[]): [string, string, string][] {
  const bounds = new Map<string, [string, string]>();
  for (const [chart, , first, last] of plans) {
    const binding = chart.binding!;
    if (binding.kind !== 'scalar_inputs') continue;
    for (const item of binding.inputs) {
      const old = bounds.get(item.measure_id) ?? [first, last];
      bounds.set(item.measure_id, [[first, old[0]].sort()[0]!, [last, old[1]].sort().at(-1)!]);
    }
  }
  return [...bounds]
    .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
    .map(([id, [first, last]]) => [id, first, last]);
}
export function category_read_ranges(
  sql: ReadSql,
  goal: ReadingSubject,
  now: Date,
  options: ReadOptions = {},
): [[string, string, string][], number] {
  const [, , selected] = plans(sql, goal, now, options);
  return [
    measure_ranges(selected),
    selected.reduce((sum, [, , first, last]) => sum + length(first, last), 0),
  ];
}
function scalar_values(
  sql: ReadSql,
  definitions: Definitions,
  ranges: [string, string, string][],
  now: number,
): [MeasureValue[], Map<string, Set<string>>] {
  check_goal_demand_count(
    ranges.reduce((sum, [, first, last]) => sum + length(first, last), 0),
    MAX_DEMANDED_MEASURE_DATES,
  );
  const roots: MeasureDate[] = ranges.flatMap(([measure_id, first, last]) =>
    date_range(first, last).map((date) => ({ measure_id, date })),
  );
  const evaluation = read_goal_evaluation(sql, roots, definitions, now, MAX_DEMANDED_MEASURE_DATES);
  const submitted = new Map(
    ranges.map(([id, first, last]) => [id, read_recorded_dates(sql, id, first, last)]),
  );
  const values = roots.map((pair) => {
    const definition = definitions.get(pair.measure_id)!;
    return goal_actual_value(
      definition,
      pair,
      evaluation,
      local_date(now, definition.timezone),
      submitted.get(pair.measure_id)!.has(pair.date),
    );
  });
  return [values, submitted];
}
export function category_projections(
  sql: ReadSql,
  goal: ReadingSubject,
  now: Date,
  options: ReadOptions = {},
): [Record<string, CategoryChartProjection>, SourceHealthSummary[]] {
  const [, definitions, selected] = plans(sql, goal, now, options);
  if (!selected.length) return [{}, []];
  const instant = Math.floor(now.getTime() / 1000);
  const [values, submitted] = scalar_values(sql, definitions, measure_ranges(selected), instant);
  const dataset_ranges = new Map<string, [string, string]>();
  for (const [chart, , first, last] of selected) {
    const binding = chart.binding!;
    if (binding.kind === 'dataset') {
      const prior = dataset_ranges.get(binding.dataset_id) ?? [first, last];
      dataset_ranges.set(binding.dataset_id, [
        [first, prior[0]].sort()[0]!,
        [last, prior[1]].sort().at(-1)!,
      ]);
    }
  }
  const retained = new Map<string, ReturnType<typeof datasets.read_values>>();
  const health = new Map(
    read_health(
      sql,
      [
        ...new Set(
          [...definitions.values()]
            .map((item) => item.source_id)
            .filter((id): id is string => id !== null),
        ),
      ].sort(),
      instant,
    ).map((item) => [item.source_id, item]),
  );
  for (const [identifier, [first_date, last_date]] of dataset_ranges) {
    const reading = datasets.read_values(
      sql,
      { dataset_ids: [identifier], first_date, last_date },
      instant,
    );
    retained.set(identifier, reading);
    for (const item of reading.sources) health.set(item.source_id, item);
  }
  const projections: Record<string, CategoryChartProjection> = {};
  for (const [chart, earliest] of selected) {
    const binding = chart.binding!;
    if (binding.kind === 'dataset')
      projections[chart.id] = dataset_chart_projection(chart, retained.get(binding.dataset_id)!, {
        now: instant,
        earliest_date: earliest,
        ...options,
      });
    else {
      const identifiers = new Set(binding.inputs.map((item) => item.measure_id));
      projections[chart.id] = scalar_inputs_chart_projection(
        chart,
        definitions,
        values.filter((value) => identifiers.has(value.measure_id)),
        { now: instant, earliest_date: earliest, submitted_dates: submitted, ...options },
      );
    }
  }
  return [projections, [...health.values()] as SourceHealthSummary[]];
}
