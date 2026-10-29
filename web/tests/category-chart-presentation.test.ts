import { describe, expect, it } from 'vitest';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import type { CategoryChartProjection, CategoryChartRange, CategoryChartSummary, CategoryChartValue, ChartBlock } from '../src/lib/goals/contracts';
import { allocationData, categoryDetails, chartData, chartHeadline, chartTone, comparisonText, formatAxisTick, hasCountAxis, selectedSeries } from '../src/lib/charts/presentation';
import { ProgressCard, TimeBudgetCard } from '../src/lib/charts/components/goal-indicators';
import { GoalChart } from '../src/lib/charts/GoalChart';

const chart: ChartBlock = {
  type: 'chart', id: 'categories', kind: 'line', placement: 'primary',
  binding: { kind: 'dataset', dataset_id: 'sales', group_by: 'team', filters: [], aggregation: 'sum', metric: { numerator: 'won', denominator: 'offered' } },
  direction: 'atleast', target: 0.5, format: { decimals: 2 },
};
const reading = (value: number | null, partial = false, reasons: string[] = []): CategoryChartValue => ({
  status: value === null ? 'missing_input' : 'value', value, numerator: null, denominator: null,
  partial, missing_dates: partial ? ['2026-10-28'] : [], reasons,
});
const summary = (actual: CategoryChartValue, comparison: CategoryChartValue | null, difference: number | null, meets: boolean | null, date: string | null): CategoryChartSummary => ({
  actual, comparison, difference, meets_expectation: meets, date,
});
const dateRange: CategoryChartRange = {
  id: 'all', label: 'All time', first_date: '2026-10-28', last_date: '2026-10-29', axis: 'date',
  series: [
    { key: 'category:actual', label: 'Actual', summary: reading(0.4), points: [
      { key: '2026-10-28', label: '2026-10-28', date: '2026-10-28', actual: reading(0.3) },
      { key: '2026-10-29', label: '2026-10-29', date: '2026-10-29', actual: reading(0.6) },
    ] },
    { key: 'category:target', label: 'Target', summary: reading(0.8), points: [
      { key: '2026-10-28', label: '2026-10-28', date: '2026-10-28', actual: reading(0.7) },
      { key: '2026-10-29', label: '2026-10-29', date: '2026-10-29', actual: reading(0.9) },
    ] },
  ],
  summary: summary(reading(0.41), reading(0.5), -0.09, false, null),
  point_summaries: [
    { key: '2026-10-28', summary: summary(reading(0.32), reading(0.5), -0.18, false, '2026-10-28') },
    { key: '2026-10-29', summary: summary(reading(0.55), reading(0.5), 0.05, true, '2026-10-29') },
  ],
};
const projection = (range: CategoryChartRange): CategoryChartProjection => ({ kind: 'category', today: '2026-10-29', timezone: 'UTC', ranges: [range], dataset_results: [], measure_values: [] });
const retainedResult = (): CategoryChartProjection['dataset_results'][number] => ({
  dataset_id: 'sales', date: '2026-10-29', rows: [], query_start_at: null, query_end_at: Date.parse('2026-10-29T18:00:00Z') / 1000,
  unavailable_reason: null, limitation: 'Partial interval',
  provenance: { run_id: 'run', source_id: 'source', source_revision: 1, script_revision: 1, script_sha256: 'hash', definition_revision: 1, collected_at: 0 },
  output: { dataset_id: 'sales', definition_revision: 1, timezone: 'UTC', dimensions: [], fields: [], required_date: '2026-10-29' },
});

describe('category chart projection adapters', () => {
  it('uses supplied range and point summaries, preserving namespaced keys and corrected labels', () => {
    const source = projection(dateRange);
    const data = chartData(chart, [], 'all', source);
    expect(data.map(item => item.key)).toEqual(['2026-10-28', '2026-10-29']);
    expect(data[0].values).toEqual({ 'category:actual': 0.3, 'category:target': 0.7, target: 0.5 });
    expect(data[0].comparisons).toBeUndefined();
    expect(data.map(item => item.position)).toEqual([0, 1]);
    expect(chartHeadline(chart, data, null, source)).toMatchObject({ value: 0.41, target: 0.5, categorySummary: { difference: -0.09 } });
    expect(chartHeadline(chart, data, '2026-10-29', source)).toMatchObject({ value: 0.55, label: '2026-10-29' });
    expect(comparisonText(chart, 0.41, 0.5, undefined, dateRange.summary)).toBe('0.09 below target');
    const corrected = projection({ ...dateRange, series: dateRange.series.map(item => item.key === 'category:actual' ? { ...item, label: 'Revised Actual', points: item.points.map(point => point.key === '2026-10-29' ? { ...point, actual: reading(0.8) } : point) } : item), point_summaries: dateRange.point_summaries.map(item => item.key === '2026-10-29' ? { ...item, summary: summary(reading(0.75), reading(0.5), 0.25, true, '2026-10-29') } : item) });
    expect(selectedSeries(chart, 'all', corrected)[0]).toMatchObject({ key: 'category:actual', name: 'Revised Actual' });
    expect(chartHeadline(chart, chartData(chart, [], 'all', corrected), '2026-10-29', corrected).value).toBe(0.75);
    const week: CategoryChartRange = { ...dateRange, id: 'week', label: 'Week', summary: summary(reading(0.62), reading(0.5), 0.12, true, null) };
    const ranged = { ...source, ranges: [week, dateRange] };
    expect(chartHeadline(chart, chartData(chart, [], 'week', ranged), null, ranged, 'week').value).toBe(0.62);
    expect(chartHeadline(chart, chartData(chart, [], 'week', ranged), 'removed-key', ranged, 'week').value).toBe(0.62);
  });

  it('keeps category codes as opaque inspection keys even when they resemble dates or reserved series', () => {
    const grouped: ChartBlock = { ...chart, kind: 'bar' };
    const range: CategoryChartRange = {
      ...dateRange, axis: 'category',
      series: [{ key: 'actual', label: 'Current', summary: reading(9), points: [
        { key: 'category:2026-10-29', label: 'Revised label', date: null, actual: reading(4) },
        { key: 'category:target', label: 'Target code', date: null, actual: reading(5) },
      ] }],
      summary: summary(reading(9), null, null, null, null),
      point_summaries: [
        { key: 'category:2026-10-29', summary: summary(reading(4), null, null, null, null) },
        { key: 'category:target', summary: summary(reading(5), null, null, null, null) },
      ],
    };
    const source = projection(range), data = chartData(grouped, [], 'all', source);
    expect(data.map(item => item.axisLabel)).toEqual(['Revised label', 'Target code']);
    expect(data.map(item => item.position)).toEqual([undefined, undefined]);
    expect(chartHeadline(grouped, data, 'category:target', source)).toMatchObject({ value: 5, label: 'Target code' });
  });

  it('keeps partial verdicts neutral and reports provider limits beside the reading', () => {
    const partial = summary(reading(0.45, true, ['Provider returned a partial interval.']), reading(0.5), -0.05, null, null);
    expect(chartTone(chart, 0.45, 0.5, undefined, partial)).toBe('neutral');
    expect(comparisonText(chart, 0.45, 0.5, undefined, partial)).toBe('Partial difference: -0.05');
    const pointsChart: ChartBlock = { ...chart, unit: '%', difference_unit: 'pp', comparison_decimals: 1 };
    expect(comparisonText(pointsChart, 45, 50, undefined, summary(reading(45, true), reading(50), -5, null, null))).toBe('Partial difference: -5.0 pp');
    expect(comparisonText(pointsChart, 45, 50, undefined, summary(reading(45), reading(50), -5, false, null))).toBe('5.0 pp below target');
    expect(categoryDetails(partial)).toContain('Provider returned a partial interval.');
    expect(categoryDetails(partial).join(' ')).toContain('1 missing date: 2026-10-28');
    expect(comparisonText(chart, 0.45, undefined, undefined, { ...partial, comparison: null, difference: null })).toBe('Partial total');
    expect(chartData(chart, [], 'all')).toEqual([]);
    expect(chartHeadline(chart, [], null, undefined).value).toBeUndefined();
  });

  it('keeps partial summaries concise and exact query bounds in accessible inspection details', () => {
    const source = projection({ ...dateRange, summary: summary(reading(0.45, true), reading(0.5), -0.05, null, null) });
    source.dataset_results = [retainedResult()];
    const details = categoryDetails(source.ranges[0].summary, source);
    expect(details.join(' ')).toContain('no supplied start restriction');
    expect(details.join(' ')).not.toContain('all earlier records');
    source.ranges[0].summary.actual.reasons = ['Provider returned a partial interval.', 'The query covers only part of 2026-10-29.'];
    for (const placement of ['primary', 'supporting'] as const) {
      const markup = renderToStaticMarkup(createElement(GoalChart, {
        block: { ...chart, placement }, values: [], projection: source, narrow: true, preferenceKey: `coverage-${placement}`,
      }));
      const inspection = markup.match(/<details\b[\s\S]*?<\/details>/)?.[0];
      expect(inspection).toContain('<summary>Observation details</summary>');
      expect(inspection).toContain('Provider returned a partial interval.');
      expect(inspection).toContain('no supplied start restriction');
      expect(inspection).toContain('2026-10-29T18:00:00.000Z');
      const visible = markup.replace(/<details\b[\s\S]*?<\/details>/g, '');
      expect(visible.match(/Partial difference:/g)).toHaveLength(1);
      expect(visible).not.toContain('Provider returned');
      expect(visible).not.toContain('The query covers');
      expect(visible).not.toContain('2026-10-29T18:00:00.000Z');
      if (placement === 'supporting') {
        const header = markup.match(/<button class="support-summary"[\s\S]*?<\/button>/)?.[0];
        expect(header).not.toContain('<details');
        expect(markup.indexOf('<details')).toBeGreaterThan(markup.indexOf('</button>'));
      }
    }
    source.dataset_results[0].query_start_at = Date.parse('2026-10-29T10:00:00Z') / 1000;
    expect(categoryDetails(source.ranges[0].summary, source).join(' ')).toContain('2026-10-29T10:00:00.000Z');
  });

  it('uses whole count ticks and preserves distinct fractional ticks for amounts and ratios', () => {
    const source = projection(dateRange);
    source.dataset_results = [retainedResult()];
    const countChart: ChartBlock = { ...chart, binding: { kind: 'dataset', dataset_id: 'sales', group_by: 'team', filters: [], aggregation: 'sum', metric: { field: 'won' } } };
    source.dataset_results[0].output.fields = [{ name: 'won', label: 'Won', unit: 'tickets', kind: 'count' }];
    expect(hasCountAxis(countChart, source)).toBe(true);
    expect(hasCountAxis(chart, source)).toBe(false);
    source.ranges[0] = { ...dateRange, summary: summary(reading(2), null, null, null, null), series: [{ key: 'actual', label: 'Won', summary: reading(2), points: [
      { key: '2026-10-28', label: '2026-10-28', date: '2026-10-28', actual: reading(0) },
      { key: '2026-10-29', label: '2026-10-29', date: '2026-10-29', actual: reading(2) },
    ] }], point_summaries: [] };
    const countMarkup = renderToStaticMarkup(createElement(GoalChart, { block: countChart, values: [], projection: source, narrow: false, preferenceKey: 'count-axis' }));
    expect([...countMarkup.matchAll(/<b>(.*?)<\/b>/g)].map(match => match[1])).toEqual(['0', '1', '2']);
    source.dataset_results[0].output.fields[0].kind = 'amount';
    expect(hasCountAxis(countChart, source)).toBe(false);
    const fractionalMarkup = renderToStaticMarkup(createElement(GoalChart, { block: countChart, values: [], projection: source, narrow: false, preferenceKey: 'amount-axis' }));
    expect([...fractionalMarkup.matchAll(/<b>(.*?)<\/b>/g)].map(match => match[1])).toEqual(['0', '0.5', '1', '1.5', '2']);
    expect([0, 0.25, 0.5, 0.75, 1].map(value => formatAxisTick(chart, value))).toEqual(['0', '0.25', '0.5', '0.75', '1']);
    expect(formatAxisTick({ ...chart, unit: '%' }, 0.025)).toBe('0.025%');
    expect(formatAxisTick({ ...chart, format: { prefix: '€' }, stacked: true }, 0.25)).toBe('€0.25');
    expect(formatAxisTick({ ...chart, unit: 'tickets' }, 1000)).toBe('1000');
  });

  it('uses supplied allocation totals and excludes former snapshot members', () => {
    const allocation: ChartBlock = { ...chart, kind: 'allocation', binding: { ...chart.binding!, aggregation: 'latest' } };
    const range: CategoryChartRange = {
      ...dateRange, axis: 'date',
      series: [
        { key: 'category:2026-10-29', label: 'Date code', summary: reading(7), points: [{ key: '2026-10-29', label: '2026-10-29', date: '2026-10-29', actual: reading(4) }] },
        { key: 'category:old', label: 'Former', summary: null, points: [{ key: '2026-10-29', label: '2026-10-29', date: '2026-10-29', actual: reading(null) }] },
      ],
      summary: summary(reading(10), null, null, null, '2026-10-29'),
      point_summaries: [{ key: '2026-10-29', summary: summary(reading(6), null, null, null, '2026-10-29') }],
    };
    const data = chartData(allocation, [], 'all', projection(range));
    const series = selectedSeries(allocation, 'all', projection(range));
    expect(allocationData(data, series, range)).toMatchObject({ total: 10, series: [{ key: 'category:2026-10-29' }], reading: { values: { 'category:2026-10-29': 7 } } });
    expect(allocationData(data, series, range, '2026-10-29')).toMatchObject({ total: 6, reading: { values: { 'category:2026-10-29': 4 } } });
  });

  it('keeps partial indicator geometry neutral and missing plans finite', () => {
    const partial = summary(reading(7, true), reading(10), -3, null, '2026-10-29');
    const progress = renderToStaticMarkup(createElement(ProgressCard, { block: { ...chart, kind: 'progress', target: 10 }, value: 7, summary: partial }));
    expect(progress).toContain('var(--chart-muted)');
    const budget = renderToStaticMarkup(createElement(TimeBudgetCard, { block: { ...chart, kind: 'time-budget', target: 10 }, value: 7, summary: partial }));
    expect(budget).toContain('var(--chart-muted)');
    const missing = renderToStaticMarkup(createElement(TimeBudgetCard, { block: { ...chart, kind: 'time-budget', target: undefined }, value: 7, summary: summary(reading(7), reading(null), null, null, null) }));
    expect(missing).not.toMatch(/NaN|Infinity/);
    expect(missing).toContain('Planned reading unavailable');
  });

  it('uses the supplied planned reading for a named scalar budget target', () => {
    const budget: ChartBlock = {
      ...chart, kind: 'time-budget', target: 999,
      binding: { kind: 'scalar_inputs', aggregation: 'latest', inputs: [
        { key: 'spent', label: 'Spent', measure_id: 'spent', role: 'spent' },
        { key: 'planned', label: 'Planned', measure_id: 'planned', role: 'planned' },
      ] },
    };
    const range: CategoryChartRange = {
      ...dateRange, series: [], summary: summary(reading(7), reading(10), -3, true, '2026-10-29'), point_summaries: [],
    };
    const source = projection(range);
    const current = chartHeadline(budget, chartData(budget, [], 'all', source), null, source);
    expect(current).toMatchObject({ value: 7, target: 10 });
    expect(chartTone(budget, current.value, current.target, current.point, current.categorySummary)).toBe('good');
    const partial = summary(reading(7, true), reading(10), -3, null, '2026-10-29');
    expect(chartTone(budget, 7, 10, undefined, partial)).toBe('neutral');
  });
});
