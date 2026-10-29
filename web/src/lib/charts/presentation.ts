import type { ChartBlock, ChartSeries, MeasureValue, GoalResponse, ScalarChartProjection, ScalarChartPoint } from '../goals/contracts';
import type { LineChartDatum } from './components/line-chart-integrated';

export type ChartRangeSelection = 'week' | 'month' | 'all' | string;

export function formatReading(block: ChartBlock, value: number): string {
  if (!Number.isFinite(value)) return 'No reading';
  return `${block.format?.prefix ?? ''}${value.toLocaleString('en-US', {
    maximumFractionDigits: block.format?.decimals ?? 0,
    minimumFractionDigits: block.format?.decimals ?? 0,
  })}${block.format?.suffix ?? block.unit ?? ''}`;
}

/** Compact readings keep fractional precision without padding whole numbers. */
export function formatTrimmedReading(block: ChartBlock, value: number): string {
  if (!Number.isFinite(value)) return 'No reading';
  return `${block.format?.prefix ?? ''}${value.toLocaleString('en-US', {
    maximumFractionDigits: block.format?.decimals ?? 0,
  })}${block.format?.suffix ?? block.unit ?? ''}`;
}

/** Axis labels use the approved abbreviated scale; readings retain full precision. */
export function formatAxisTick(block: ChartBlock, value: number): string {
  const prefix = block.format?.prefix ?? '';
  const suffix = block.format?.suffix ?? block.unit ?? '';
  if (prefix && (block.stacked || Math.abs(value) >= 1000)) return `${prefix}${(value / 1000).toLocaleString('en-US', { maximumFractionDigits: 1 })}k${suffix}`;
  if (suffix === '%') return `${Number(value.toFixed(1))}%`;
  if (prefix) return `${prefix}${Math.round(value).toLocaleString('en-US')}${suffix}`;
  return String(Math.round(value));
}

export function selectedSeries(block: ChartBlock, range: string): ChartSeries[] {
  const series = block.ranges?.find(item => item.id === range)?.series ?? block.series ?? block.ranges?.[0]?.series ?? [];
  return block.measure_id ? series.slice(0, 1) : series;
}

export function selectedTarget(block: ChartBlock, range: string): number | undefined {
  const selected = block.ranges?.find(item => item.id === range) ?? (!block.series ? block.ranges?.[0] : undefined);
  return selected?.target ?? block.target;
}

export function selectedDomain(block: ChartBlock, range: string): ChartBlock['domain'] {
  const selected = block.ranges?.find(item => item.id === range) ?? (!block.series ? block.ranges?.[0] : undefined);
  return selected?.domain ?? block.domain;
}

export function chartData(block: ChartBlock, values: MeasureValue[], range: ChartRangeSelection, projection?: ScalarChartProjection): LineChartDatum[] {
  if (block.expectation) {
    if (!projection) return [];
    const cutoff = Date.parse(projection.today) - (range === 'week' ? 6 : 29) * 86400000;
    const points = projection.points.filter(point => range !== 'week' && range !== 'month' || point.date <= projection.today && Date.parse(point.date) >= cutoff);
    const first = Date.parse(points[0]?.date ?? ''), last = Date.parse(points.at(-1)?.date ?? '');
    return points.map(point => ({
      key: point.date, label: point.date,
      axisLabel: new Date(point.date + 'T12:00:00Z').toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' }),
      ...(last > first ? { position: (Date.parse(point.date) - first) / (last - first) } : {}),
      values: { actual: point.actual.status === 'value' ? point.actual.value ?? undefined : undefined, target: point.expected.status === 'value' ? point.expected.value ?? undefined : undefined },
      breakBefore: { target: point.break_before },
      comparisons: { actual: point.meets_expectation },
    }));
  }
  const selected = selectedSeries(block, range);
  const target = selectedTarget(block, range);
  const missingCategories = Object.fromEntries((block.measure_id ? [] : selected).map((series, index) => [index === 0 ? 'actual' : series.name, undefined]));
  let data: LineChartDatum[];
  if (block.measure_id) {
    const actual = values.filter(v => v.measure_id === block.measure_id).sort((a, b) => a.date.localeCompare(b.date));
    const latest = actual.at(-1)?.date;
    const cutoff = latest ? Date.parse(latest) - (range === 'week' ? 6 : range === 'month' ? 29 : Infinity) * 86400000 : -Infinity;
    data = actual.filter(v => Date.parse(v.date) >= cutoff).map(v => ({
      key: v.date, label: v.date,
      axisLabel: new Date(v.date + 'T12:00:00Z').toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' }),
      values: { actual: v.value ?? undefined },
    }));
  } else {
    const points = new Map<string, LineChartDatum>();
    selected.forEach((series, index) => series.points.forEach(p => {
      const d = points.get(p.label) ?? { key: p.label, label: p.axis ?? p.label, axisLabel: p.axis ?? p.label, values: {} };
      d.values[index === 0 ? 'actual' : series.name] = p.value ?? undefined;
      points.set(p.label, d);
    }));
    data = [...points.values()];
  }
  const first = Date.parse(data[0]?.key ?? ''), last = Date.parse(data.at(-1)?.key ?? '');
  return data.map(d => ({
    ...d,
    ...(Number.isFinite(first) && last > first ? { position: (Date.parse(d.key) - first) / (last - first) } : {}),
    values: { ...missingCategories, ...d.values, ...(block.reference?.length ? { target: referenceAt(block, d.key) } : target !== undefined ? { target } : {}) },
  }));
}

function referenceAt(block: ChartBlock, date: string): number | undefined {
  const refs = [...(block.reference ?? [])].sort((a, b) => a.date.localeCompare(b.date));
  const exact = refs.find(r => r.date === date);
  if (exact) return exact.value;
  const before = refs.filter(r => r.date < date).at(-1), after = refs.find(r => r.date > date);
  if (!before || !after) return undefined;
  return before.value + (after.value - before.value) * (Date.parse(date) - Date.parse(before.date)) / (Date.parse(after.date) - Date.parse(before.date));
}

export function readChartPreference(key: string): { range: string; expanded: boolean } {
  try {
    const v = JSON.parse(sessionStorage.getItem(key) ?? 'null');
    return { range: typeof v?.range === 'string' ? v.range : 'month', expanded: v?.expanded === true };
  } catch { return { range: 'month', expanded: false }; }
}

/** Inspection follows identity, so same-date corrections replace the displayed value. */
export function inspectedReading(data: LineChartDatum[], activeKey: string | null): LineChartDatum | undefined {
  return data.find(d => d.key === activeKey) ?? data.at(-1);
}

/** A composition uses one shared observation, never each category's last known value. */
export function compositionTotal(data: LineChartDatum[], reading: LineChartDatum | undefined): number | undefined {
  const keys = [...new Set(data.flatMap(d => Object.keys(d.values)))].filter(key => key !== 'target');
  if (!reading || !keys.length || keys.some(key => reading.values[key] === undefined)) return undefined;
  return keys.reduce((sum, key) => sum + reading.values[key]!, 0);
}

/** Whole-percent summaries preserve the total and put the largest category first. */
export function allocationSummary(series: ChartSeries[], reading: LineChartDatum | undefined): string {
  const values = series.map((item, index) => ({ name: item.name, index, value: reading?.values[index === 0 ? 'actual' : item.name] }));
  if (values.some(item => item.value === undefined)) return 'Current reading unavailable';
  const total = values.reduce((sum, item) => sum + item.value!, 0);
  if (total <= 0) return '';
  const shares = values.map(item => ({ ...item, exact: item.value! / total * 100, percent: Math.floor(item.value! / total * 100) }));
  const remaining = 100 - shares.reduce((sum, item) => sum + item.percent, 0);
  [...shares].sort((a, b) => (b.exact - b.percent) - (a.exact - a.percent) || a.index - b.index).slice(0, remaining).forEach(item => item.percent++);
  return shares.sort((a, b) => b.exact - a.exact || a.index - b.index).map(item => `${item.percent}% ${item.name.toLowerCase()}`).join(' · ');
}

export interface ChartHeadline { value: number | undefined; target: number | undefined; label: string | undefined; missingCount: number; point?: ScalarChartPoint }

export function chartHeadline(block: ChartBlock, data: LineChartDatum[], activeKey: string | null = null, projection?: ScalarChartProjection): ChartHeadline {
  if (block.expectation) {
    const reading = data.find(item => item.key === (activeKey ?? projection?.summary_date));
    return { value: reading?.values.actual, target: reading?.values.target, label: reading?.label, missingCount: data.filter(item => item.values.actual === undefined).length, point: projection?.points.find(point => point.date === reading?.key) };
  }
  const reading = inspectedReading(data, activeKey);
  const missingCount = data.filter(d => d.values.actual === undefined).length;
  if (block.stacked || block.kind === 'allocation') {
    return { value: compositionTotal(data, reading), target: reading?.values.target, label: reading?.label, missingCount };
  }
  if (block.aggregate === 'sum' && !activeKey) {
    const sum = (key: string) => {
      const known = data.flatMap(d => d.values[key] === undefined ? [] : [d.values[key]!]);
      return known.length ? known.reduce((a, b) => a + b, 0) : undefined;
    };
    return { value: sum('actual'), target: sum('target'), label: undefined, missingCount };
  }
  const value = reading?.values.actual;
  return { value: (block.kind === 'progress' || block.kind === 'time-budget') && value !== undefined && value < 0 ? undefined : value, target: reading?.values.target, label: reading?.label, missingCount };
}

export function chartTone(block: ChartBlock, value: number | undefined, target: number | undefined, point?: ScalarChartPoint): GoalResponse['status'] {
  if (block.expectation) return point?.meets_expectation == null ? 'neutral' : point.meets_expectation ? 'good' : block.below_target_tone ?? 'bad';
  if (value === undefined || target === undefined) return 'neutral';
  const good = (block.direction ?? (block.kind === 'time-budget' ? 'atmost' : 'atleast')) === 'atmost' ? value <= target : value >= target;
  return good ? 'good' : block.below_target_tone ?? 'bad';
}

export function comparisonText(block: ChartBlock, value: number | undefined, target: number | undefined, point?: ScalarChartPoint): string {
  if (block.expectation && (point?.difference == null || point.meets_expectation == null)) return '';
  if (value === undefined || target === undefined) return '';
  const signedDifference = block.expectation ? point!.difference! : value - target;
  const difference = Math.abs(signedDifference);
  const decimals = block.comparison_decimals ?? block.format?.decimals ?? 0;
  const amount = block.difference_unit === 'pp'
    ? `${difference.toLocaleString('en-US', { minimumFractionDigits: decimals, maximumFractionDigits: decimals })} pp`
    : formatReading({ ...block, format: { ...block.format, decimals } }, difference);
  if (block.kind === 'progress') return value === target ? 'Target reached' : amount + (value < target ? ' remaining' : ' beyond target');
  if (block.kind === 'time-budget') return value === target ? 'Budget used' : amount + (value < target ? ' remaining' : ' over budget');
  const label = block.target_label ?? (block.direction === 'atmost' ? 'limit' : 'target');
  if ((block.expectation ? signedDifference === 0 : difference < .0001) && block.kind !== 'bar' && !block.aggregate) return `On ${label}`;
  if (signedDifference < 0 && block.direction === 'atmost' && block.within_limit_label === 'remaining') return `${amount} remaining`;
  if (signedDifference < 0 && block.direction !== 'atmost' && label === 'target' && block.shortfall_label === 'to target') return `${amount} to target`;
  return `${amount} ${block.direction === 'atmost' ? (signedDifference <= 0 ? 'under' : 'over') : (signedDifference >= 0 ? 'above' : 'below')} ${label}`;
}

export function missingReadingText(data: LineChartDatum[], label?: string): string {
  if (!data.some(d => Object.entries(d.values).some(([key, value]) => key !== 'target' && value !== undefined))) return 'No readings yet';
  return label ? `No reading for ${label}` : 'No readings in this range';
}
