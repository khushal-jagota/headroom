/** Pure projections of retained dataset rows and named scalar inputs. */
import type {
  ChartBlock,
  DatasetChartBinding,
  ScalarInputsChartBinding,
  CategoryChartValue,
  CategoryChartSummary,
  CategoryChartProjection,
  CategoryChartRange,
  CategoryChartSeries,
  CategoryChartPoint,
  CategoryPointSummary,
  MeasureValue,
} from '../../../web/src/lib/goals/contracts.ts';
import type { Dataset, DatasetResult, DatasetRow } from '../../datasets/contracts.ts';
import type { Definitions, Coverage } from '../../measures/contracts.ts';
import { combine_coverage } from '../../measures/logic/coverage.ts';
import {
  calendar_date,
  date_range,
  offset_date,
  local_date,
  local_midnight,
  invalid,
} from '../../measures/logic/dates.ts';
import { numeric_projection, text_order } from '../category_contracts.ts';
import type { ChartInput, DatasetReading } from '../category_contracts.ts';
import { MAX_SCALAR_PROJECTION_DATES } from '../configuration.ts';
function fail(message: string): never {
  throw new TypeError(message);
}
const nonempty = (value: string, name: string): void => {
  if (!value.trim()) fail(`${name} must be nonempty`);
};
export function validate_category_chart(input: ChartInput): void {
  const chart = numeric_projection<ChartBlock>(input);
  const binding = chart.binding;
  if (!binding) return;
  if (['measure_id', 'expectation', 'manual', 'series', 'ranges'].some((key) => key in chart))
    fail('a chart binding cannot combine other live or authored inputs');
  if ('aggregate' in chart) fail('a bound chart selects aggregation inside its binding');
  if (chart.kind === 'binary') fail('binary charts do not accept numeric bindings');
  if (!['sum', 'latest'].includes(binding.aggregation)) fail('unknown binding aggregation');
  if (binding.kind === 'dataset') {
    nonempty(binding.dataset_id, 'dataset ID');
    nonempty(binding.group_by, 'group-by dimension');
    const dimensions = binding.filters.map((item) => item.dimension);
    if (new Set(dimensions).size !== dimensions.length)
      fail('dataset filter dimensions must be unique');
    dimensions.forEach((dimension) => nonempty(dimension, 'filter dimension'));
    const metric = binding.metric;
    if ('field' in metric) nonempty(metric.field, 'metric field');
    else {
      nonempty(metric.numerator, 'numerator field');
      nonempty(metric.denominator, 'denominator field');
      if (!Number.isFinite(metric.scale ?? 1)) fail('ratio scale must be finite');
      if (chart.stacked || !['bar', 'line', 'number', 'gauge'].includes(chart.kind))
        fail('ratio metrics require unstacked bars, lines, or scalar readouts');
    }
    if (chart.kind === 'time-budget')
      fail('a live time budget requires named spent and planned inputs');
  } else {
    const inputs = binding.inputs,
      keys = inputs.map((item) => item.key);
    if (!inputs.length || new Set(keys).size !== keys.length)
      fail('named scalar input keys must be nonempty and unique');
    inputs.forEach((item) => {
      nonempty(item.key, 'input key');
      nonempty(item.label, 'input label');
      nonempty(item.measure_id, 'input measure ID');
    });
    const roles = inputs.map((item) => item.role);
    if (chart.kind === 'time-budget') {
      if (inputs.length !== 2 || !roles.includes('spent') || !roles.includes('planned'))
        fail('time budgets require one spent and one planned input');
    } else if (roles.some((role) => role !== undefined))
      fail('spent and planned roles belong to time-budget charts');
  }
}
export function validate_category_references(
  input: ChartInput,
  datasets: Map<string, Dataset>,
  measures: Definitions,
): void {
  const chart = numeric_projection<ChartBlock>(input),
    binding = chart.binding;
  if (!binding) return;
  validate_category_chart(chart);
  if (binding.kind === 'scalar_inputs') {
    if (binding.inputs.some((item) => !measures.has(item.measure_id)))
      fail('bound input measure does not exist');
    return;
  }
  const definition = datasets.get(binding.dataset_id);
  if (!definition) fail('bound dataset does not exist');
  const dimensions = new Set(definition.dimensions.map((item) => item.name));
  if (
    [binding.group_by, ...binding.filters.map((item) => item.dimension)].some(
      (key) => !dimensions.has(key),
    )
  )
    fail('binding references an unknown dataset dimension');
  const fields = new Set(definition.fields.map((item) => item.name));
  if (metric_fields(binding).some((key) => !fields.has(key)))
    fail('binding references an unknown dataset field');
}
export function category_bounds(
  today: string,
  earliest_date: string | null,
  first_date: string | null = null,
  last_date: string | null = null,
): ['week' | 'month' | 'all', string, string][] {
  calendar_date(today);
  const anchor = last_date ?? today;
  const anchorDate = calendar_date(anchor);
  if (first_date !== null && calendar_date(first_date).getTime() > anchorDate.getTime())
    throw invalid('goal date range is reversed');
  const earliest = earliest_date ?? anchor;
  calendar_date(earliest);
  const ordinal = (anchorDate.getTime() - calendar_date('0001-01-01').getTime()) / 86400000 + 1;
  return (['week', 'month', 'all'] as const).map((key) => {
    let first =
      key === 'all'
        ? (first_date ?? [earliest, anchor].sort()[0]!)
        : offset_date(anchor, -Math.min(ordinal - 1, key === 'week' ? 6 : 29));
    if (key !== 'all' && first_date !== null) first = [first, first_date].sort().at(-1)!;
    if (
      (anchorDate.getTime() - calendar_date(first).getTime()) / 86400000 + 1 >
      MAX_SCALAR_PROJECTION_DATES
    )
      throw invalid(
        `goal projection exceeds ${MAX_SCALAR_PROJECTION_DATES} dates per chart; request a narrower date range`,
      );
    return [key, first, anchor];
  });
}
const key = (family: string, id: string): string => family + ':' + JSON.stringify(id);
function metric_fields(binding: DatasetChartBinding): string[] {
  return 'field' in binding.metric
    ? [binding.metric.field]
    : [binding.metric.numerator, binding.metric.denominator];
}
function value(
  raw: number | null,
  options: Partial<Omit<CategoryChartValue, 'value'>> = {},
): CategoryChartValue {
  let status = options.status ?? 'value';
  let reasons = options.reasons ?? [];
  if (raw !== null && !Number.isFinite(raw)) {
    raw = null;
    status = 'arithmetic_limit';
    reasons = [...reasons, 'The calculated value exceeds the numeric limit.'];
  }
  return {
    status,
    value: raw,
    numerator: options.numerator ?? null,
    denominator: options.denominator ?? null,
    partial: options.partial ?? false,
    missing_dates: [...new Set(options.missing_dates ?? [])].sort(text_order),
    reasons: [...new Set(reasons)],
    ...('coverage_status' in options ? { coverage_status: options.coverage_status } : {}),
  };
}
function missing(reason: string, day: string | null = null): CategoryChartValue {
  return value(null, {
    status: 'missing_input',
    missing_dates: day === null ? [] : [day],
    reasons: [reason],
  });
}
/** Accumulate a partial expansion, then apply the final half-even correction. */
function sum(values: number[]): number | null {
  const partials: number[] = [];
  for (let x of values) {
    let i = 0;
    for (let y of partials) {
      if (Math.abs(x) < Math.abs(y)) [x, y] = [y, x];
      const hi = x + y;
      if (!Number.isFinite(hi)) return null;
      const lo = y - (hi - x);
      if (lo !== 0) partials[i++] = lo;
      x = hi;
    }
    partials.length = i;
    if (x !== 0) partials.push(x);
  }
  let n = partials.length;
  let hi = 0,
    lo = 0;
  if (n) {
    hi = partials[--n]!;
    while (n > 0) {
      const x = hi,
        y = partials[--n]!;
      hi = x + y;
      lo = y - (hi - x);
      if (lo !== 0) break;
    }
    if (n > 0 && ((lo < 0 && partials[n - 1]! < 0) || (lo > 0 && partials[n - 1]! > 0))) {
      const y = lo * 2,
        x = hi + y;
      if (y === x - hi) hi = x;
    }
  }
  return Number.isFinite(hi) ? hi : null;
}
function combine(
  values: CategoryChartValue[],
  {
    ratio = false,
    scale = 1,
    require_all = false,
  }: { ratio?: boolean; scale?: number; require_all?: boolean } = {},
): CategoryChartValue {
  const usable = values.filter((item) =>
    ratio
      ? item.numerator !== null && item.denominator !== null
      : item.status === 'value' && item.value !== null,
  );
  const missing_dates = values.flatMap((item) => item.missing_dates),
    reasons = values
      .flatMap((item) => item.reasons)
      .filter((reason) => reason !== 'The denominator is zero.');
  const partial = values.some((item) => item.partial) || usable.length !== values.length;
  const coverage = values.some((item) => 'coverage_status' in item)
    ? {
        coverage_status: combine_coverage(
          values.map(
            (item) => [item.coverage_status ?? 'not_applicable', []] as [Coverage, string[]],
          ),
        )[0],
      }
    : {};
  if (!usable.length || (require_all && usable.length !== values.length))
    return value(null, {
      status: values.find((item) => item.status !== 'value')?.status ?? 'missing_input',
      partial: partial && usable.length > 0,
      missing_dates,
      reasons: reasons.length ? reasons : ['No usable inputs exist in this range.'],
      ...coverage,
    });
  let numerator: number | null = null,
    denominator: number | null = null,
    actual: number | null;
  if (ratio) {
    numerator = sum(usable.map((item) => item.numerator!));
    denominator = sum(usable.map((item) => item.denominator!));
    if (numerator !== null && denominator === 0)
      return value(null, {
        status: 'division_by_zero',
        numerator,
        denominator,
        partial,
        missing_dates,
        reasons: [...reasons, 'The denominator is zero.'],
        ...coverage,
      });
    actual = numerator !== null && denominator !== null ? (numerator / denominator) * scale : null;
  } else actual = sum(usable.map((item) => item.value!));
  return value(actual, {
    status: actual !== null ? 'value' : 'arithmetic_limit',
    numerator,
    denominator,
    partial,
    missing_dates,
    reasons: actual !== null ? reasons : [...reasons, 'The sum exceeds the numeric limit.'],
    ...coverage,
  });
}
function quantity(chart: ChartBlock, actual: CategoryChartValue): CategoryChartValue {
  if (
    ['allocation', 'time-budget', 'progress'].includes(chart.kind) &&
    actual.value !== null &&
    actual.value < 0
  )
    return {
      ...actual,
      status: 'invalid_value',
      value: null,
      reasons: [...actual.reasons, 'This chart requires a nonnegative quantity.'],
    };
  return actual;
}
function reference(chart: ChartBlock, day: string | null): CategoryChartValue | null {
  const references = [...(chart.reference ?? [])].sort((a, b) => text_order(a.date, b.date));
  if (references.length) {
    if (day === null) return null;
    const exact = references.find((item) => item.date === day);
    if (exact) return value(exact.value);
    const before = references.filter((item) => item.date < day).at(-1),
      after = references.find((item) => item.date > day);
    if (!before || !after) return missing(`No reference covers ${day}.`);
    const fraction =
      (calendar_date(day).getTime() - calendar_date(before.date).getTime()) /
      (calendar_date(after.date).getTime() - calendar_date(before.date).getTime());
    return value(before.value + (after.value - before.value) * fraction);
  }
  return 'target' in chart ? value(chart.target!) : null;
}
function summary(
  chart: ChartBlock,
  actual: CategoryChartValue,
  day: string | null,
  comparison: CategoryChartValue | null = null,
): CategoryChartSummary {
  if (comparison === null) comparison = reference(chart, day);
  if (
    chart.kind === 'time-budget' &&
    comparison !== null &&
    comparison.value !== null &&
    comparison.value <= 0
  )
    comparison = {
      ...comparison,
      value: null,
      status: 'invalid_value',
      reasons: [...comparison.reasons, 'The planned quantity must be positive.'],
    };
  let difference: number | null = null,
    meets_expectation: boolean | null = null;
  if (
    actual.status === 'value' &&
    actual.value !== null &&
    comparison !== null &&
    comparison.status === 'value' &&
    comparison.value !== null
  ) {
    const delta = actual.value - comparison.value;
    difference = Number.isFinite(delta) ? delta : null;
    const direction = chart.direction ?? (chart.kind === 'time-budget' ? 'atmost' : 'atleast');
    meets_expectation =
      direction === 'atmost' ? actual.value <= comparison.value : actual.value >= comparison.value;
  }
  return { actual, comparison, difference, meets_expectation, date: day };
}
function coverage(result: DatasetResult, snapshot: boolean): [boolean, string[]] {
  const reasons = result.limitation !== null ? [result.limitation] : [];
  if (!snapshot) {
    const day = result.date;
    const start = local_midnight(day, result.output.timezone),
      end =
        day < '9999-12-31' ? local_midnight(offset_date(day, 1), result.output.timezone) : Infinity;
    if (
      result.query_end_at < end ||
      (result.query_start_at !== null && result.query_start_at > start)
    )
      reasons.push(`The query covers only part of ${day}.`);
    if (
      result.query_end_at > end ||
      (result.query_start_at !== null && result.query_start_at < start)
    )
      reasons.push(`The query interval extends outside ${day}.`);
  }
  return [reasons.length > 0, reasons];
}
function groups(result: DatasetResult, binding: DatasetChartBinding): Map<string, DatasetRow[]> {
  const grouped = new Map<string, DatasetRow[]>();
  for (const row of result.rows)
    if (
      binding.group_by in row.dimensions &&
      binding.filters.every((item) => item.codes.includes(row.dimensions[item.dimension]!))
    ) {
      const code = row.dimensions[binding.group_by]!;
      if (!grouped.has(code)) grouped.set(code, []);
      grouped.get(code)!.push(row);
    }
  return grouped;
}
function dataset_value(
  chart: ChartBlock,
  binding: DatasetChartBinding,
  result: DatasetResult | undefined,
  rows: DatasetRow[],
  day: string,
  category = false,
): CategoryChartValue {
  if (!result) return missing(`No dataset result exists for ${day}.`, day);
  if (result.unavailable_reason !== null) return missing(`${day}: ${result.unavailable_reason}`);
  const dimensions = new Set(result.output.dimensions.map((item) => item.name)),
    fields = new Map(result.output.fields.map((item) => [item.name, item]));
  const absent = [
    ...new Set(
      [...binding.filters.map((item) => item.dimension), binding.group_by]
        .filter((name) => !dimensions.has(name))
        .concat(metric_fields(binding).filter((name) => !fields.has(name))),
    ),
  ].sort(text_order);
  if (absent.length)
    return missing(`The retained declaration for ${day} lacks: ${absent.join(', ')}.`);
  const required = metric_fields(binding),
    snapshot = required.some((name) => fields.get(name)!.kind === 'snapshot');
  const [partial, reasons] = coverage(
    result,
    required.every((name) => fields.get(name)!.kind === 'snapshot'),
  );
  if (!rows.length && category && (partial || snapshot || binding.aggregation === 'latest'))
    return value(null, {
      status: 'missing_input',
      partial,
      reasons: [...reasons, `The category has no usable value on ${day}.`],
    });
  if (!rows.length && snapshot)
    return value(null, {
      status: 'missing_input',
      partial,
      reasons: [...reasons, `The snapshot for ${day} is empty.`],
    });
  if (rows.some((row) => required.some((name) => !(name in row.values))))
    return missing(`A required numeric field is absent from a retained row on ${day}.`);
  const metric = binding.metric;
  let actual: CategoryChartValue;
  if ('field' in metric) {
    const quantities = rows.map((row) => value(row.values[metric.field]!));
    actual = quantities.length ? combine(quantities) : value(0);
  } else {
    const numerator = sum(rows.map((row) => row.values[metric.numerator]!)),
      denominator = sum(rows.map((row) => row.values[metric.denominator]!));
    const paired = value(null, {
      status: numerator !== null && denominator !== null ? 'value' : 'arithmetic_limit',
      numerator,
      denominator,
      reasons:
        numerator !== null && denominator !== null ? [] : ['The sum exceeds the numeric limit.'],
    });
    actual = combine([paired], { ratio: true, scale: metric.scale ?? 1 });
  }
  actual = {
    ...actual,
    partial: actual.partial || partial,
    reasons: [...new Set([...actual.reasons, ...reasons])],
  };
  return quantity(chart, actual);
}
function range(
  id: 'week' | 'month' | 'all',
  first: string,
  last: string,
  series: CategoryChartSeries[],
  headline: CategoryChartSummary,
  points: CategoryPointSummary[],
  grouped: boolean,
): CategoryChartRange {
  return {
    id,
    label: { week: 'Week', month: 'Month', all: 'All time' }[id],
    first_date: first,
    last_date: last,
    axis: grouped ? 'category' : 'date',
    series,
    summary: headline,
    point_summaries: points,
  };
}
export interface ProjectionOptions {
  now: number;
  earliest_date: string | null;
  first_date?: string | null;
  last_date?: string | null;
}
export function dataset_chart_projection(
  input: ChartInput,
  reading: DatasetReading,
  options: ProjectionOptions,
): CategoryChartProjection {
  const chart = numeric_projection<ChartBlock>(input);
  validate_category_chart(chart);
  const binding = chart.binding;
  if (!binding || binding.kind !== 'dataset') fail('dataset projection requires a dataset binding');
  const definition = reading.datasets.find((item) => item.id === binding.dataset_id);
  if (!definition) throw invalid('dataset projection requires its current definition');
  if (reading.truncated)
    throw invalid('a goal projection requires a complete dataset read; request a narrower range');
  const today = local_date(options.now, definition.timezone),
    bounds = category_bounds(
      today,
      options.earliest_date,
      options.first_date ?? null,
      options.last_date ?? null,
    );
  const lower = bounds.map((item) => item[1]).sort()[0]!,
    upper = bounds
      .map((item) => item[2])
      .sort()
      .at(-1)!;
  if (reading.first_date > lower || reading.last_date < upper)
    throw invalid('dataset read does not cover the complete projection interval');
  const retained_results = new Map(
    reading.results
      .filter((item) => item.dataset_id === definition.id)
      .map((item) => [item.date, item]),
  );
  const results = new Map(
    [...retained_results].map(([day, item]) => [day, numeric_projection<DatasetResult>(item)]),
  );
  const grouped_rows = new Map([...results].map(([day, item]) => [day, groups(item, binding)]));
  const labels = new Map(
    reading.labels
      .filter((item) => item.dataset_id === definition.id && item.dimension === binding.group_by)
      .map((item) => [item.code, item.label]),
  );
  const metric = binding.metric,
    ratio = !('field' in metric),
    scale = 'field' in metric ? 1 : (metric.scale ?? 1);
  const grouped = chart.kind === 'bar' && !chart.stacked;
  const ranges: CategoryChartRange[] = [];
  for (const [id, first, last] of bounds) {
    const days = date_range(first, last),
      selected =
        [...results.keys()]
          .filter((day) => first <= day && day <= last)
          .sort()
          .at(-1) ?? null;
    const codes = [
      ...new Set(days.flatMap((day) => [...(grouped_rows.get(day)?.keys() ?? [])])),
    ].sort(text_order);
    const day_totals = new Map(
      days.map((day) => [
        day,
        dataset_value(
          chart,
          binding,
          results.get(day),
          [...(grouped_rows.get(day)?.values() ?? [])].flat(),
          day,
        ),
      ]),
    );
    const dated = new Map(
      codes.map((code) => [
        code,
        new Map(
          days.map((day) => [
            day,
            dataset_value(
              chart,
              binding,
              results.get(day),
              grouped_rows.get(day)?.get(code) ?? [],
              day,
              true,
            ),
          ]),
        ),
      ]),
    );
    let total: CategoryChartValue,
      totals: Map<string, CategoryChartValue | null>,
      summary_date: string | null;
    if (binding.aggregation === 'latest') {
      total =
        selected !== null
          ? day_totals.get(selected)!
          : missing('No dataset snapshot exists in this range.');
      const members = new Set(selected !== null ? grouped_rows.get(selected)?.keys() : []);
      totals = new Map(
        codes.map((code) => [
          code,
          selected !== null && members.has(code) ? dated.get(code)!.get(selected)! : null,
        ]),
      );
      summary_date = selected;
    } else {
      total = combine([...day_totals.values()], { ratio, scale });
      totals = new Map(
        codes.map((code) => [code, combine([...dated.get(code)!.values()], { ratio, scale })]),
      );
      summary_date = null;
    }
    const headline = summary(chart, total, summary_date),
      series: CategoryChartSeries[] = [],
      inspections: CategoryPointSummary[] = [];
    if (grouped) {
      const points: CategoryChartPoint[] = [];
      for (const code of codes) {
        const actual = totals.get(code);
        if (actual == null) continue;
        const point_key = key('category', code);
        points.push({ key: point_key, label: labels.get(code) ?? code, date: null, actual });
        inspections.push({ key: point_key, summary: summary(chart, actual, summary_date) });
      }
      series.push({ key: 'actual', label: chart.title ?? definition.name, points, summary: total });
    } else {
      for (const code of codes)
        series.push({
          key: key('category', code),
          label: labels.get(code) ?? code,
          points: days.map((day) => ({
            key: day,
            label: day,
            date: day,
            actual: dated.get(code)!.get(day)!,
          })),
          summary: totals.get(code)!,
        });
      inspections.push(
        ...days.map((day) => ({ key: day, summary: summary(chart, day_totals.get(day)!, day) })),
      );
    }
    ranges.push(range(id, first, last, series, headline, inspections, grouped));
  }
  return {
    kind: 'category',
    today,
    timezone: definition.timezone,
    ranges,
    dataset_results: [...retained_results]
      .sort(([a], [b]) => text_order(a, b))
      .filter(([day]) => lower <= day && day <= upper)
      .map(([, result]) => result),
    measure_values: [],
  };
}
function scalar_value(
  chart: ChartBlock,
  observed: MeasureValue | undefined,
  identifier: string,
  day: string,
  submitted: boolean,
): CategoryChartValue {
  if (!observed)
    return missing(`No value for ${identifier} exists on ${day}.`, submitted ? null : day);
  const reasons =
    observed.status === 'value' ? [] : [`${identifier} on ${day}: ${observed.status}.`];
  return quantity(
    chart,
    value(observed.value, {
      status: observed.status,
      partial: observed.coverage_status === 'partial',
      missing_dates: observed.value === null && !submitted ? [day] : [],
      reasons: [...reasons, ...observed.coverage_reasons],
      coverage_status: observed.coverage_status,
    }),
  );
}
function scalar_summary(
  chart: ChartBlock,
  binding: ScalarInputsChartBinding,
  values: Map<string, CategoryChartValue>,
  day: string | null,
): CategoryChartSummary {
  if (chart.kind === 'time-budget') {
    const spent = binding.inputs.find((item) => item.role === 'spent')!.key,
      planned = binding.inputs.find((item) => item.role === 'planned')!.key;
    return summary(chart, values.get(spent)!, day, values.get(planned)!);
  }
  return summary(
    chart,
    combine([...values.values()], { require_all: binding.aggregation === 'latest' }),
    day,
  );
}
export function scalar_inputs_chart_projection(
  input: ChartInput,
  definitions: Definitions,
  values: MeasureValue[],
  options: ProjectionOptions & { submitted_dates: Map<string, Set<string>> },
): CategoryChartProjection {
  const chart = numeric_projection<ChartBlock>(input);
  validate_category_chart(chart);
  const binding = chart.binding;
  if (!binding || binding.kind !== 'scalar_inputs')
    fail('named scalar projection requires a scalar_inputs binding');
  validate_category_references(chart, new Map(), definitions);
  const zone = definitions.get(binding.inputs[0]!.measure_id)!.timezone,
    today = local_date(options.now, zone),
    bounds = category_bounds(
      today,
      options.earliest_date,
      options.first_date ?? null,
      options.last_date ?? null,
    );
  const identifiers = new Set(binding.inputs.map((item) => item.measure_id));
  const actuals = new Map(
    values
      .filter((item) => identifiers.has(item.measure_id))
      .map((item) => [JSON.stringify([item.measure_id, item.date]), item]),
  );
  const candidates = new Set(
    [...identifiers].flatMap((id) => [...(options.submitted_dates.get(id) ?? [])]),
  );
  for (const observed of values)
    if (
      identifiers.has(observed.measure_id) &&
      definitions.get(observed.measure_id)!.origin === 'assembled' &&
      observed.status === 'value' &&
      observed.date <= local_date(options.now, definitions.get(observed.measure_id)!.timezone)
    )
      candidates.add(observed.date);
  const grouped = chart.kind === 'bar' && !chart.stacked,
    ranges: CategoryChartRange[] = [];
  for (const [id, first, last] of bounds) {
    const days = date_range(first, last),
      selected =
        [...candidates]
          .filter((day) => first <= day && day <= last)
          .sort()
          .at(-1) ?? null;
    const dated = new Map(
      binding.inputs.map((item) => [
        item.key,
        new Map(
          days.map((day) => [
            day,
            scalar_value(
              chart,
              actuals.get(JSON.stringify([item.measure_id, day])),
              item.measure_id,
              day,
              options.submitted_dates.get(item.measure_id)?.has(day) ?? false,
            ),
          ]),
        ),
      ]),
    );
    const totals = new Map(
      binding.inputs.map((item) => [
        item.key,
        binding.aggregation === 'latest'
          ? selected !== null
            ? dated.get(item.key)!.get(selected)!
            : missing(`No snapshot for ${item.label} exists in this range.`)
          : combine([...dated.get(item.key)!.values()]),
      ]),
    );
    const summary_date = binding.aggregation === 'latest' ? selected : null,
      headline = scalar_summary(chart, binding, totals, summary_date),
      series: CategoryChartSeries[] = [],
      inspections: CategoryPointSummary[] = [];
    if (grouped) {
      const points: CategoryChartPoint[] = [];
      for (const item of binding.inputs) {
        const point_key = key('input', item.key);
        points.push({
          key: point_key,
          label: item.label,
          date: null,
          actual: totals.get(item.key)!,
        });
        inspections.push({
          key: point_key,
          summary: summary(chart, totals.get(item.key)!, summary_date),
        });
      }
      series.push({
        key: 'actual',
        label: chart.title ?? 'Reading',
        points,
        summary: headline.actual,
      });
    } else {
      for (const item of binding.inputs)
        series.push({
          key: key('input', item.key),
          label: item.label,
          points: days.map((day) => ({
            key: day,
            label: day,
            date: day,
            actual: dated.get(item.key)!.get(day)!,
          })),
          summary: totals.get(item.key)!,
        });
      inspections.push(
        ...days.map((day) => ({
          key: day,
          summary: scalar_summary(
            chart,
            binding,
            new Map(binding.inputs.map((item) => [item.key, dated.get(item.key)!.get(day)!])),
            day,
          ),
        })),
      );
    }
    ranges.push(range(id, first, last, series, headline, inspections, grouped));
  }
  const lower = bounds.map((item) => item[1]).sort()[0]!,
    upper = bounds
      .map((item) => item[2])
      .sort()
      .at(-1)!;
  return {
    kind: 'category',
    today,
    timezone: zone,
    ranges,
    dataset_results: [],
    measure_values: values.filter(
      (item) => identifiers.has(item.measure_id) && lower <= item.date && item.date <= upper,
    ),
  };
}
