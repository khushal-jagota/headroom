import { prepare_document, validate_chart } from './documents.ts';
/** The thirteen Python rule groups protect retained category arithmetic and coverage. */
import { expect, test } from 'vitest';
import type {
  ChartBlock,
  CategoryChartProjection,
  MeasureValue,
} from '../../../web/src/lib/goals/contracts.ts';
import type { Dataset, DatasetField, DatasetResult, DatasetRow } from '../../datasets/contracts.ts';
import type { Measure } from '../../measures/contracts.ts';
import type { DatasetReading } from '../category_contracts.ts';
import {
  category_bounds,
  dataset_chart_projection,
  scalar_inputs_chart_projection,
  validate_category_chart,
  validate_category_references,
} from './categories.ts';
const NOW = Date.parse('2026-10-29T12:00:00Z') / 1000;
const FIELDS: DatasetField[] = ['n', 'd'].map((name) => ({
  name,
  label: name,
  unit: '',
  kind: 'count',
}));
function dataset(fields = FIELDS): Dataset {
  return {
    id: 'dataset',
    project_id: 'project',
    source_id: 'source',
    name: 'Counts',
    timezone: 'UTC',
    dimensions: [
      { name: 'surface', label: 'Surface' },
      { name: 'country', label: 'Country' },
    ],
    fields,
    definition_revision: 2,
    created_at: NOW,
    updated_at: NOW,
  };
}
function result(day: string, rows: DatasetRow[] = [], fields = FIELDS): DatasetResult {
  return {
    dataset_id: 'dataset',
    date: day,
    rows,
    query_end_at: Date.parse(day + 'T00:00:00Z') / 1000 + 86400,
    query_start_at: null,
    unavailable_reason: null,
    limitation: null,
    provenance: {
      run_id: 'run-' + day,
      source_id: 'source',
      source_revision: 2,
      script_revision: 3,
      script_sha256: 'sha',
      definition_revision: 1,
      collected_at: NOW,
    },
    output: {
      dataset_id: 'dataset',
      definition_revision: 1,
      timezone: 'UTC',
      dimensions: dataset().dimensions,
      fields,
      required_date: day,
    },
  };
}
function row(code: string, n: number, d = 1, country = 'US'): DatasetRow {
  return { dimensions: { surface: code, country }, values: { n, d } };
}
function dataset_chart(
  options: {
    aggregation?: 'sum' | 'latest';
    kind?: ChartBlock['kind'];
    metric?: { field: string } | { numerator: string; denominator: string; scale?: number };
    filters?: { dimension: string; codes: string[] }[];
  } = {},
): ChartBlock {
  return {
    type: 'chart',
    id: 'chart',
    kind: options.kind ?? 'bar',
    placement: 'primary',
    binding: {
      kind: 'dataset',
      dataset_id: 'dataset',
      group_by: 'surface',
      filters: options.filters ?? [],
      aggregation: options.aggregation ?? 'sum',
      metric: options.metric ?? { field: 'n' },
    },
  };
}
function project(
  chart: ChartBlock,
  results: DatasetResult[],
  options: {
    definition?: Dataset;
    first?: string;
    last?: string;
    labels?: DatasetReading['labels'];
  } = {},
): CategoryChartProjection {
  const reading: DatasetReading = {
    first_date: '2026-09-01',
    last_date: '2026-10-31',
    datasets: [options.definition ?? dataset()],
    results,
    labels: options.labels ?? [],
    missing_dates: [],
    sources: [],
    returned_rows: results.reduce((sum, item) => sum + item.rows.length, 0),
    matching_rows: results.reduce((sum, item) => sum + item.rows.length, 0),
    truncated: false,
  };
  return dataset_chart_projection(chart, reading, {
    now: NOW,
    earliest_date: results.map((item) => item.date).sort()[0] ?? null,
    first_date: options.first ?? '2026-10-28',
    last_date: options.last ?? '2026-10-29',
  });
}
const selected = (projection: CategoryChartProjection, id = 'all') =>
  projection.ranges.find((item) => item.id === id)!;
function measure(id: string): Measure {
  return {
    id,
    project_id: 'project',
    name: id,
    unit: 'h',
    origin: 'manual',
    kind: 'reading',
    timezone: 'UTC',
    period: null,
    source_id: null,
    formula: null,
    definition_revision: 1,
    write_revision: 0,
    created_at: NOW,
    updated_at: NOW,
  };
}
function scalar(id: string, day: string, value: number | null): MeasureValue {
  return {
    measure_id: id,
    date: day,
    definition_revision: 1,
    status: value === null ? 'missing_input' : 'value',
    value,
    inputs: [],
    missing_inputs: [],
    query_start_at: null,
    query_end_at: null,
    limitation: null,
    coverage_status: 'not_applicable',
    coverage_reasons: [],
  };
}
function scalar_chart(
  kind: ChartBlock['kind'] = 'allocation',
  aggregation: 'sum' | 'latest' = 'latest',
): ChartBlock {
  return {
    type: 'chart',
    id: 'scalar-chart',
    kind,
    placement: 'primary',
    binding: {
      kind: 'scalar_inputs',
      aggregation,
      inputs: [
        {
          key: 'actual',
          label: 'Spent',
          measure_id: 'spent',
          ...(kind === 'time-budget' ? { role: 'spent' as const } : {}),
        },
        {
          key: 'target',
          label: 'Planned',
          measure_id: 'planned',
          ...(kind === 'time-budget' ? { role: 'planned' as const } : {}),
        },
      ],
    },
  };
}
function scalar_project(
  chart: ChartBlock,
  values: MeasureValue[],
  submitted: Map<string, Set<string>>,
) {
  return scalar_inputs_chart_projection(
    chart,
    new Map(['spent', 'planned'].map((key) => [key, measure(key)])),
    values,
    {
      now: NOW,
      earliest_date: '2026-10-28',
      submitted_dates: submitted,
      first_date: '2026-10-28',
      last_date: '2026-10-29',
    },
  );
}
const both = () =>
  new Map(['spent', 'planned'].map((key) => [key, new Set(['2026-10-28', '2026-10-29'])]));
test('joint filters weight ratios and preserve code identity across ranges', () => {
  const chart = dataset_chart({
    metric: { numerator: 'n', denominator: 'd', scale: 100 },
    filters: [
      { dimension: 'surface', codes: ['actual', 'target'] },
      { dimension: 'country', codes: ['US'] },
    ],
  });
  const results = [
    result('2026-10-01', [row('actual', 1, 2), row('actual', 100, 1, 'DE')]),
    result('2026-10-28', [row('target', 9, 10), row('other', 500, 1)]),
  ];
  const projection = project(chart, results, {
    first: '2026-10-01',
    last: '2026-10-28',
    labels: ['actual', 'target'].map((code) => ({
      dataset_id: 'dataset',
      dimension: 'surface',
      code,
      label: 'Renamed',
    })),
  });
  const total = selected(projection).summary.actual;
  expect([total.numerator, total.denominator]).toEqual([10, 12]);
  expect(total.value).toBeCloseTo(1000 / 12);
  expect(selected(projection, 'week').summary.actual.value).toBe(90);
  const points = selected(projection).series[0]!.points;
  expect(points.map((item) => item.key)).toEqual(['category:"actual"', 'category:"target"']);
  expect(points.map((item) => item.label)).toEqual(['Renamed', 'Renamed']);
  expect(points.every((item) => item.date === null)).toBe(true);
  const dated = project({ ...chart, kind: 'line' }, results, {
    first: '2026-10-01',
    last: '2026-10-28',
  });
  expect(selected(dated).series.map((item) => item.key)).toEqual(points.map((item) => item.key));
  expect(dated.dataset_results).toEqual(results);
});
test('empty history uses captured names and missing dates never become zero', () => {
  const fields = [...FIELDS, { name: 'new', label: 'New', unit: '', kind: 'count' as const }];
  const projection = project(
    dataset_chart({ kind: 'line', metric: { field: 'new' } }),
    [result('2026-10-27'), result('2026-10-28', [], fields)],
    { definition: dataset(fields), first: '2026-10-27' },
  );
  const inspections = selected(projection).point_summaries;
  expect(inspections[0]!.summary.actual.status).toBe('missing_input');
  expect(inspections[0]!.summary.actual.reasons[0]).toContain('lacks: new');
  expect(inspections[1]!.summary.actual.value).toBe(0);
  expect(inspections[2]!.summary.actual.missing_dates).toEqual(['2026-10-29']);
  expect(selected(projection).summary.actual.value).toBe(0);
  expect(selected(projection).summary.actual.partial).toBe(true);
  const retained = project(dataset_chart(), [result('2026-10-29', [row('old', 6)])], {
    definition: dataset([fields.at(-1)!]),
    first: '2026-10-29',
  });
  expect(selected(retained).summary.actual.value).toBe(6);
});
test('latest snapshot uses one date and excludes disappeared current members', () => {
  const fields: DatasetField[] = [{ name: 'n', label: 'Balance', unit: 'USD', kind: 'snapshot' }];
  const older = result('2026-10-28', [row('actual', 100), row('target', 50)], fields),
    latest = { ...result('2026-10-29', [row('actual', 120)], fields), query_end_at: NOW };
  const chart = dataset_chart({ aggregation: 'latest', kind: 'allocation' });
  const view = selected(project(chart, [older, latest], { definition: dataset(fields) }));
  expect(view.summary.date).toBe('2026-10-29');
  expect(view.summary.actual.value).toBe(120);
  expect(view.summary.actual.partial).toBe(false);
  expect(view.series[1]!.summary).toBeNull();
  expect(view.series[1]!.points.map((point) => point.actual.value)).toEqual([50, null]);
  const empty = result('2026-10-30', [], fields),
    empty_view = selected(
      project(chart, [older, latest, empty], { definition: dataset(fields), last: '2026-10-30' }),
    );
  expect(empty_view.summary.date).toBe('2026-10-30');
  expect(empty_view.summary.actual.value).toBeNull();
  const unavailable = selected(
    project(chart, [latest, { ...empty, unavailable_reason: 'Provider unavailable' }], {
      definition: dataset(fields),
      last: '2026-10-30',
    }),
  );
  expect(unavailable.summary.actual.reasons[0]).toContain('Provider unavailable');
});
test('partial additive intervals keep subtotals without zero for omitted members', () => {
  const older = result('2026-10-28', [row('actual', 10), row('target', 20)]),
    latest = {
      ...result('2026-10-29', [row('actual', 5)]),
      query_end_at: NOW,
      query_start_at: NOW - 6 * 3600,
      limitation: 'Sampled population',
    };
  const chart = { ...dataset_chart({ kind: 'line' }), target: 40 };
  const view = selected(project(chart, [older, latest]));
  expect(view.summary.actual.value).toBe(35);
  expect(view.summary.actual.partial).toBe(true);
  expect(view.summary.difference).toBe(-5);
  expect(view.summary.meets_expectation).toBe(false);
  expect(view.series[1]!.points[1]!.actual.value).toBeNull();
  expect(view.summary.actual.reasons).toContain('Sampled population');
  const complete = selected(project(chart, [older, result('2026-10-29', latest.rows)]));
  expect(complete.series[1]!.points[1]!.actual.value).toBe(0);
  expect(complete.summary.actual.partial).toBe(false);
  expect(complete.summary.meets_expectation).toBe(false);
});
test('mixed ratio keeps additive coverage without changing snapshot emptiness', () => {
  for (const kind of ['count', 'snapshot'] as const) {
    const fields: DatasetField[] = [
        { name: 'n', label: 'Numerator', unit: '', kind },
        { name: 'd', label: 'Denominator', unit: '', kind: 'snapshot' },
      ],
      observed = { ...result('2026-10-29', [row('actual', 5, 10)], fields), query_end_at: NOW };
    const chart = {
      ...dataset_chart({
        aggregation: 'latest',
        kind: 'number',
        metric: { numerator: 'n', denominator: 'd' },
      }),
      target: 0.4,
    };
    const summary = selected(project(chart, [observed], { first: '2026-10-29' })).summary;
    expect(summary.actual.value).toBe(0.5);
    expect(summary.difference).toBeCloseTo(0.1);
    expect(summary.actual.partial).toBe(kind === 'count');
    expect(summary.meets_expectation).toBe(true);
    const empty = selected(project(chart, [{ ...observed, rows: [] }], { first: '2026-10-29' }));
    expect(empty.summary.actual.status).toBe('missing_input');
    expect(empty.summary.actual.value).toBeNull();
  }
});
test('named latest keeps unavailable submission and dynamic budget comparison', () => {
  const values = [
    scalar('spent', '2026-10-28', 3),
    scalar('planned', '2026-10-28', 8),
    scalar('spent', '2026-10-29', 4),
    scalar('planned', '2026-10-29', null),
  ];
  const budget = selected(scalar_project(scalar_chart('time-budget'), values, both()));
  expect(budget.summary.date).toBe('2026-10-29');
  expect(budget.summary.actual.value).toBe(4);
  expect(budget.summary.comparison?.value).toBeNull();
  expect(budget.summary.difference).toBeNull();
  const allocation = selected(scalar_project(scalar_chart(), values, both()));
  expect(allocation.summary.actual.value).toBeNull();
  expect(allocation.series.map((item) => item.key)).toEqual(['input:"actual"', 'input:"target"']);
  expect(allocation.series[0]!.summary).not.toBeNull();
  const good = selected(
    scalar_project(
      scalar_chart('time-budget'),
      values.slice(0, 2),
      new Map(['spent', 'planned'].map((key) => [key, new Set(['2026-10-28'])])),
    ),
  );
  expect(good.last_date).toBe('2026-10-29');
  expect(good.summary.date).toBe('2026-10-28');
  expect(good.summary.difference).toBe(-5);
  expect(good.summary.meets_expectation).toBe(true);
});
test('named additive summary supplies each allocation quantity and partial budget', () => {
  const values = [
    scalar('spent', '2026-10-28', 2),
    scalar('planned', '2026-10-28', 8),
    scalar('spent', '2026-10-29', 3),
    scalar('planned', '2026-10-29', null),
  ];
  const submitted = both();
  submitted.set('planned', new Set(['2026-10-28']));
  const allocation = selected(scalar_project(scalar_chart('allocation', 'sum'), values, submitted));
  expect(allocation.summary.actual.value).toBe(13);
  expect(allocation.summary.actual.partial).toBe(true);
  expect(allocation.series.map((item) => item.summary?.value ?? null)).toEqual([5, 8]);
  expect(allocation.summary.date).toBeNull();
  const budget = selected(scalar_project(scalar_chart('time-budget', 'sum'), values, submitted));
  expect(budget.summary.actual.value).toBe(5);
  expect(budget.summary.difference).toBe(-3);
  expect(budget.summary.meets_expectation).toBe(true);
});
test('ratio zero denominator and arithmetic limit remain explicit', () => {
  const chart = dataset_chart({ metric: { numerator: 'n', denominator: 'd', scale: 100 } });
  const view = selected(
    project(chart, [result('2026-10-29', [row('actual', 1, 0), row('target', 9, 10)])], {
      first: '2026-10-29',
    }),
  );
  expect(view.summary.actual.value).toBe(100);
  expect(view.series[0]!.points[0]!.actual.status).toBe('division_by_zero');
  const zero = selected(
    project(chart, [result('2026-10-29', [row('actual', 5, 0)])], { first: '2026-10-29' }),
  ).summary.actual;
  expect([zero.status, zero.value, zero.numerator, zero.denominator]).toEqual([
    'division_by_zero',
    null,
    5,
    0,
  ]);
  for (const current of [chart, dataset_chart()]) {
    const overflow = selected(
      project(current, [result('2026-10-29', [row('a', 1e308), row('b', 1e308)])], {
        first: '2026-10-29',
      }),
    ).summary.actual;
    expect(overflow.status).toBe('arithmetic_limit');
    expect(overflow.value).toBeNull();
  }
  const extreme = selected(
    project({ ...dataset_chart(), target: -1e308 }, [result('2026-10-29', [row('a', 1e308)])], {
      first: '2026-10-29',
    }),
  ).summary;
  expect(extreme.actual.value).toBe(1e308);
  expect(extreme.difference).toBeNull();
  expect(extreme.meets_expectation).toBe(true);
});
test('bounds clip literal calendars and refuse unbounded history', () => {
  expect(category_bounds('2026-10-29', '2026-10-01', null, null)).toEqual([
    ['week', '2026-10-23', '2026-10-29'],
    ['month', '2026-09-30', '2026-10-29'],
    ['all', '2026-10-01', '2026-10-29'],
  ]);
  expect(category_bounds('0001-01-01', null, '0001-01-01', '0001-01-02')).toEqual(
    ['week', 'month', 'all'].map((key) => [key, '0001-01-01', '0001-01-02']),
  );
  expect(() => category_bounds('2026-10-29', '2000-01-01', null, null)).toThrow(/narrower/);
  expect(() => category_bounds('2026-10-29', null, '2026-10-30', null)).toThrow(/reversed/);
});
test('read limits cannot produce apparently complete totals', () => {
  const reading: DatasetReading = {
    first_date: '2026-10-28',
    last_date: '2026-10-29',
    datasets: [dataset()],
    results: [],
    labels: [],
    missing_dates: [],
    sources: [],
    returned_rows: 0,
    matching_rows: 1,
    truncated: true,
  };
  expect(() =>
    dataset_chart_projection(dataset_chart(), reading, {
      now: NOW,
      earliest_date: null,
      first_date: '2026-10-28',
    }),
  ).toThrow(/complete dataset read/);
  expect(() =>
    dataset_chart_projection(
      dataset_chart(),
      { ...reading, truncated: false },
      { now: NOW, earliest_date: null, first_date: '2026-10-27' },
    ),
  ).toThrow(/complete projection interval/);
});
test('captured timezone controls DST interval coverage', () => {
  const observed = result('2026-10-25', [row('actual', 10)]),
    full = {
      ...observed,
      output: { ...observed.output, timezone: 'Europe/Berlin' },
      query_end_at: Date.parse('2026-10-25T23:00:00Z') / 1000,
    };
  expect(
    selected(project(dataset_chart(), [full], { first: '2026-10-25', last: '2026-10-25' })).summary
      .actual.partial,
  ).toBe(false);
  const partial = selected(
    project(dataset_chart(), [{ ...full, query_end_at: full.query_end_at - 1800 }], {
      first: '2026-10-25',
      last: '2026-10-25',
    }),
  );
  expect(partial.summary.actual.value).toBe(10);
  expect(partial.summary.actual.partial).toBe(true);
});
test('binding validation rejects ambiguous geometry without kind algebra', () => {
  // Shape validation precedes the ordered per-block identity and semantic checks.
  const text_block = { type: 'text', id: 'duplicate', text: 'First' };
  const bad_chart = {
    type: 'chart',
    id: 'duplicate',
    placement: 'supporting',
    kind: 'progress',
    target: -1,
    series: [],
  };
  const ordered_page = (blocks: unknown[]) => ({
    kind: 'headroom.page',
    version: 1,
    title: '',
    summary: '',
    blocks,
  });
  expect(() => prepare_document(ordered_page([text_block, bad_chart]), () => 'unused')).toThrow(
    'page: component IDs must be nonempty and unique',
  );
  expect(() => prepare_document(ordered_page([bad_chart, text_block]), () => 'unused')).toThrow(
    'progress and time-budget charts need a positive target',
  );
  // The shared plain preparer must reject JavaScript's extra year-zero calendar.
  const checklist_page = (deadline: string) => ({
    kind: 'headroom.page',
    version: 1,
    title: '',
    summary: '',
    blocks: [
      {
        type: 'checklist',
        id: 'checklist',
        items: [{ id: 'item', label: 'One', state: 'open', deadline }],
      },
    ],
  });
  expect(() => prepare_document(checklist_page('0000-01-01'), () => 'unused')).toThrow(
    'checklist item deadline must be an ISO calendar date or null',
  );
  expect(() => prepare_document(checklist_page('0001-01-01'), () => 'unused')).not.toThrow();
  for (const date of [
    '2026-02-30',
    '2026-13-01',
    '0000-01-01',
    '20261029',
    '2026-W44-4',
    'invalid',
  ]) {
    expect(() => validate_chart({ ...dataset_chart(), reference: [{ date, value: 1 }] })).toThrow(
      'reference dates must be ISO calendar dates',
    );
  }
  const ratio = dataset_chart({ metric: { numerator: 'n', denominator: 'd' } });
  for (const chart of [
    { ...ratio, stacked: true },
    { ...ratio, kind: 'allocation' as const },
  ])
    expect(() => validate_category_chart(chart)).toThrow(/ratio metrics/);
  expect(() => validate_category_chart({ ...ratio, measure_id: 'legacy' })).toThrow(
    /other live or authored/,
  );
  expect(() =>
    validate_category_references(
      dataset_chart({ metric: { field: 'absent' } }),
      new Map([['dataset', dataset()]]),
      new Map(),
    ),
  ).toThrow(/unknown dataset field/);
  expect(() => validate_category_chart({ ...scalar_chart(), kind: 'time-budget' })).toThrow(
    /one spent and one planned/,
  );
  validate_category_references(
    dataset_chart(),
    new Map([['dataset', dataset([{ name: 'n', label: 'Snapshot', unit: '', kind: 'snapshot' }])]]),
    new Map(),
  );
  validate_category_references(
    dataset_chart({ aggregation: 'latest' }),
    new Map([['dataset', dataset()]]),
    new Map(),
  );
  validate_category_chart(
    dataset_chart({ filters: [{ dimension: 'surface', codes: ['', 'actual', 'target'] }] }),
  );
});
test('named scalar coverage survives points comparisons and totals', () => {
  for (const coverage of ['partial', 'unknown'] as const) {
    const complete = [
      scalar('spent', '2026-10-28', 2),
      scalar('planned', '2026-10-28', 8),
      scalar('spent', '2026-10-29', 3),
      scalar('planned', '2026-10-29', 8),
    ].map((item) => ({ ...item, coverage_status: 'complete' as const }));
    for (const index of [2, 3]) {
      const values: MeasureValue[] = [...complete];
      values[index] = {
        ...values[index]!,
        coverage_status: coverage,
        coverage_reasons: ['Limited input'],
      };
      for (const aggregation of ['latest', 'sum'] as const) {
        const view = selected(
          scalar_project(scalar_chart('time-budget', aggregation), values, both()),
        );
        const summary = view.summary;
        expect(summary.actual.value).toBe(aggregation === 'latest' ? 3 : 5);
        expect(summary.difference).toBe(aggregation === 'latest' ? -5 : -11);
        expect(summary.meets_expectation).toBe(true);
        const affected = index === 2 ? summary.actual : summary.comparison!;
        expect(affected.coverage_status).toBe(coverage);
        expect(affected.partial).toBe(coverage === 'partial');
        expect(affected.reasons).toContain('Limited input');
        expect(view.point_summaries.at(-1)!.summary.meets_expectation).toBe(true);
      }
      const allocation = selected(
        scalar_project(scalar_chart('allocation', 'sum'), values, both()),
      );
      expect(allocation.summary.actual.coverage_status).toBe(coverage);
      expect(allocation.summary.actual.value).toBe(21);
    }
    expect(
      selected(scalar_project(scalar_chart('time-budget'), complete, both())).summary
        .meets_expectation,
    ).toBe(true);
  }
});
