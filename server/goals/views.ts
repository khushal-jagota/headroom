import type { ReadSql } from '../core/database/contracts.ts';
import { PlannerError } from '../core/contracts.ts';
import { read_dataset } from '../datasets/data/index.ts';
import { read_values } from '../measures/actions.ts';
import {
  earliest_retained_date,
  read_definitions,
  read_health,
  read_measure,
} from '../measures/data.ts';
import type { Definitions, MeasureDate, MeasureValue } from '../measures/contracts.ts';
import { pair_key } from '../measures/contracts.ts';
import { calendar_date, date_range, invalid, local_date } from '../measures/logic/dates.ts';
import {
  ArithmeticDepthLimit,
  expression_references,
  phase_changed,
} from '../measures/logic/evaluation.ts';
import { parse_formula } from '../measures/logic/formulas.ts';
import * as limits from './configuration.ts';
import type {
  ChartBlock,
  ChartProjection,
  Goal,
  GoalResponse,
  GoalsResponse,
  ReadingSubject,
  ScalarChartProjection,
  Tone,
} from './contracts.ts';
import { list_goals, read_goal } from './data.ts';
import { calendar_parts, with_calendar_iso } from './calendar.ts';
export { calendar_now } from './calendar.ts';
import { category_projections, category_read_ranges } from './category_reads.ts';
import { effective_completion, goal_completed } from './logic/completion.ts';
import { charts, dataset_ids, expectation_measure_ids, measure_ids } from './logic/documents.ts';
import { goal_outcome, outcome_summary } from './logic/outcomes.ts';
import {
  projection_bounds,
  projection_point,
  projection_status,
  scalar_projection,
} from './logic/projection.ts';
import { check_goal_demand_count, goal_actual_value } from './logic/reading_assembly.ts';
import { chart_status, goal_status } from './logic/status.ts';
import { read_goal_evaluation } from './reading_assembly.ts';
import { ordered_record } from '../core/http/ordered_record.ts';
const goal_fields = [
  'id',
  'holder_kind',
  'holder_id',
  'project_id',
  'title',
  'why',
  'kind',
  'active',
  'completed',
  'completion_date',
  'completion_rule',
  'outcome',
  'primary_checklist_block_id',
  'deadline',
  'primary_chart',
  'page',
  'revision',
  'created_at',
  'updated_at',
  'employee_backend',
  'employee_launch_model',
  'employee_launch_reasoning_effort',
  'status',
  'status_date',
  'next_refresh_at',
] as const;
export function goal_json(_sql: ReadSql, goal: Goal, now: Date): GoalResponse {
  const [today, midnight] = calendar_parts(now),
    completed = goal_completed(goal);
  return ordered_record(
    {
      ...goal,
      completed,
      outcome: goal_outcome(goal, today),
      status:
        goal.kind === 'binary' ? goal_status(completed, goal.deadline, today) : ('neutral' as Tone),
      status_date: today,
      next_refresh_at: midnight,
    },
    goal_fields,
    'Goal',
    ['binary_checked'],
  );
}
export function list_json(
  sql: ReadSql,
  now: Date,
  options: { active?: boolean | null; project_id?: string | null } = {},
): GoalsResponse {
  const [today, midnight] = calendar_parts(now),
    all = list_goals(sql),
    active = options.active ?? null,
    project = options.project_id ?? null;
  return {
    goals: all
      .filter(
        (g) =>
          (active === null || g.active === active) &&
          (project === null || g.project_id === project),
      )
      .map((g) => goal_json(sql, g, now)),
    outcome_summary: outcome_summary(all, today),
    status_date: today,
    next_refresh_at: midnight,
  };
}
export function reading_calendar(
  sql: ReadSql,
  subject: ReadingSubject,
  now: Date,
): [string, number] {
  const [today, midnight] = calendar_parts(now),
    dates = [today];
  let next = midnight;
  const definitions: Definitions = new Map(
    measure_ids(subject.primary_chart ?? null, subject.page).map((id) => [
      id,
      read_measure(sql, id),
    ]),
  );
  for (const [id, m] of read_definitions(
    sql,
    expectation_measure_ids(subject.primary_chart ?? null, subject.page),
    null,
    true,
  ))
    definitions.set(id, m);
  for (const [id, m] of [...definitions].sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))) {
    const [day, following] = calendar_parts(now, m.timezone);
    dates.push(id + ':' + day);
    next = Math.min(next, following);
  }
  for (const id of dataset_ids(subject.primary_chart ?? null, subject.page)) {
    const [day, following] = calendar_parts(now, read_dataset(sql, id).timezone);
    dates.push(id + ':' + day);
    next = Math.min(next, following);
  }
  return [dates.join('|'), next];
}
type Range = [string, string, string];
function formula_projections(
  sql: ReadSql,
  subject: ReadingSubject,
  now: Date,
  first: string | null,
  last: string | null,
  retained: Range[],
  category_dates: number,
): [Record<string, ScalarChartProjection>, Map<string, ReturnType<typeof read_health>[number]>] {
  const formula_charts = charts(subject.primary_chart ?? null, subject.page)
    .filter((c) => c.expectation !== undefined)
    .map((c) => ({ ...c, expectation: parse_formula(c.expectation) }));
  if (!formula_charts.length) return [{}, new Map()];
  const bound_ids = [...new Set(formula_charts.map((c) => c.measure_id!))].sort(),
    bound = new Map(bound_ids.map((id) => [id, read_measure(sql, id)])),
    bounds = new Map<string, [string, string, string]>();
  let count = category_dates;
  for (const chart of formula_charts) {
    const m = bound.get(chart.measure_id!)!,
      today = local_date(now.getTime() / 1000, m.timezone);
    const [a, b, length] = projection_bounds(
      today,
      earliest_retained_date(sql, m.id),
      subject.deadline ?? null,
      first,
      last,
    );
    count += length;
    if (count > limits.MAX_PROJECTED_CHART_DATES)
      throw invalid(
        `goal projections exceed ${limits.MAX_PROJECTED_CHART_DATES} chart-date points; request a narrower date range`,
      );
    bounds.set(chart.id, [a, b, today]);
  }
  const definitions = read_definitions(sql, bound_ids, null, true);
  for (const [id, m] of read_definitions(
    sql,
    expectation_measure_ids(subject.primary_chart ?? null, subject.page),
    null,
    true,
  ))
    definitions.set(id, m);
  for (const [id, m] of read_definitions(
    sql,
    retained.map((r) => r[0]),
  ))
    definitions.set(id, m);
  const instant = Math.floor(now.getTime() / 1000),
    roots = new Map<string, MeasureDate>();
  for (const [id, a, b] of retained) {
    const length = (calendar_date(b).getTime() - calendar_date(a).getTime()) / 86400000 + 1;
    check_goal_demand_count(roots.size + length, limits.MAX_DEMANDED_MEASURE_DATES);
    for (const date of date_range(a, b)) {
      const pair = { measure_id: id, date };
      roots.set(pair_key(pair), pair);
      check_goal_demand_count(roots.size, limits.MAX_DEMANDED_MEASURE_DATES);
    }
  }
  const days_by_chart = new Map<string, string[]>();
  for (const chart of formula_charts) {
    const [a, b, today] = bounds.get(chart.id)!,
      days = date_range(a, b),
      m = bound.get(chart.measure_id!)!;
    days_by_chart.set(chart.id, days);
    for (const date of days) {
      if (date <= today || m.origin !== 'assembled') {
        const pair = { measure_id: m.id, date };
        roots.set(pair_key(pair), pair);
      }
      let references: MeasureDate[];
      try {
        references = expression_references(chart.expectation, date, m.timezone, instant);
      } catch (e) {
        if (!(e instanceof PlannerError) && !(e instanceof ArithmeticDepthLimit)) throw e;
        references = [];
      }
      for (const pair of references) {
        roots.set(pair_key(pair), pair);
        check_goal_demand_count(roots.size, limits.MAX_DEMANDED_MEASURE_DATES);
      }
    }
  }
  const evaluation = read_goal_evaluation(
      sql,
      [...roots.values()],
      definitions,
      instant,
      limits.MAX_DEMANDED_MEASURE_DATES,
    ),
    result: Record<string, ScalarChartProjection> = {};
  for (const chart of formula_charts) {
    const [a, b, today] = bounds.get(chart.id)!,
      m = bound.get(chart.measure_id!)!,
      points = days_by_chart.get(chart.id)!.map((date) => {
        const pair = { measure_id: m.id, date },
          actual = goal_actual_value(
            m,
            pair,
            evaluation,
            today,
            evaluation.observations.has(pair_key(pair)),
          ),
          expected = evaluation.expression(chart.expectation, date, m.timezone);
        return projection_point(
          date,
          actual,
          expected,
          chart.direction ?? 'atleast',
          phase_changed(chart.expectation, date, definitions),
        );
      });
    result[chart.id] = scalar_projection(a, b, today, points);
  }
  const health = read_health(
    sql,
    [
      ...new Set(
        [...definitions.values()].map((m) => m.source_id).filter((id): id is string => id !== null),
      ),
    ].sort(),
    instant,
  );
  return [result, new Map(health.map((h) => [h.source_id, h]))];
}
export function readings_json(
  sql: ReadSql,
  id: string,
  now: Date,
  options: { first_date?: string | null; last_date?: string | null } = {},
): Record<string, unknown> {
  return document_readings_json(sql, read_goal(sql, id), now, options);
}
export function document_readings_json(
  sql: ReadSql,
  subject: ReadingSubject,
  now: Date,
  options: { first_date?: string | null; last_date?: string | null; calendar_iso?: string } = {},
): Record<string, unknown> {
  now = with_calendar_iso(now, options.calendar_iso);
  const first = options.first_date ?? null,
    last = options.last_date ?? null,
    [today] = calendar_parts(now),
    [, midnight] = reading_calendar(sql, subject, now),
    identifiers = [
      ...new Set(
        charts(subject.primary_chart ?? null, subject.page)
          .filter((c) => c.measure_id !== undefined)
          .map((c) => c.measure_id!),
      ),
    ].sort(),
    retained: Range[] = [];
  for (const id of identifiers) {
    const m = read_measure(sql, id),
      b = last ?? local_date(now.getTime() / 1000, m.timezone),
      earliest = earliest_retained_date(sql, id),
      a = first ?? (earliest !== null ? (earliest < b ? earliest : b) : b);
    retained.push([id, a, b]);
  }
  const category_options = { first_date: first, last_date: last },
    [category_ranges, category_dates] = category_read_ranges(sql, subject, now, category_options),
    budget = new Map<string, [string, string]>();
  for (const [id, a, b] of [...retained, ...category_ranges]) {
    const old = budget.get(id) ?? [a, b];
    budget.set(id, [a < old[0] ? a : old[0], b > old[1] ? b : old[1]]);
  }
  const [formulas, sources] = formula_projections(
      sql,
      subject,
      now,
      first,
      last,
      [...budget].map(([id, [a, b]]) => [id, a, b]),
      category_dates,
    ),
    [categories, category_health] = category_projections(sql, subject, now, category_options);
  for (const h of category_health) sources.set(h.source_id, h);
  const projections: Record<string, ChartProjection> = { ...formulas, ...categories },
    values: MeasureValue[] = [];
  let primary: MeasureValue[] = [];
  for (const [id, a, b] of retained) {
    const reading = read_values(
      sql,
      { measure_ids: [id], first_date: a, last_date: b },
      Math.floor(now.getTime() / 1000),
    );
    values.push(...reading.values);
    for (const h of reading.sources) sources.set(h.source_id, h);
    if (subject.primary_chart?.measure_id === id) primary = reading.values;
  }
  const known = new Set(values.map(pair_key));
  for (const projection of Object.values(categories))
    for (const value of projection.measure_values)
      if (!known.has(pair_key(value))) {
        values.push(value);
        known.add(pair_key(value));
      }
  let status: Tone;
  if ((subject.kind ?? 'binary') === 'binary')
    status = goal_status(
      effective_completion(
        subject.binary_checked ?? false,
        subject.page,
        subject.primary_checklist_block_id ?? null,
      ),
      subject.deadline ?? null,
      today,
    );
  else if (subject.primary_chart && subject.primary_chart.id in projections) {
    const p = projections[subject.primary_chart.id]!;
    if (p.kind === 'scalar') status = projection_status(subject.primary_chart, p);
    else {
      const meets = p.ranges.find((r) => r.id === 'all')!.summary.meets_expectation;
      status =
        meets === null
          ? 'neutral'
          : meets
            ? 'good'
            : (subject.primary_chart.below_target_tone ?? 'bad');
    }
  } else status = chart_status(subject.primary_chart ?? null, primary);
  return {
    goal_id: subject.id,
    revision: subject.revision,
    status,
    status_date: today,
    next_refresh_at: midnight,
    values,
    sources: [...sources.values()],
    charts: projections,
  };
}
