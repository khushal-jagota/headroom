import type { ChartBlock, ChartSeries, MeasureValue, GoalResponse, ChartProjection, ScalarChartPoint, CategoryChartRange, CategoryChartSummary, CategoryChartValue } from '../goals/contracts';
import type { LineChartDatum } from './components/line-chart-integrated';

export type ChartRangeSelection = 'week' | 'month' | 'all' | string;

/** Explicit suffixes are literal; word units supply their own separator. */
export function readingSuffix(block: ChartBlock): string {
  if (block.format?.suffix !== undefined) return block.format.suffix;
  const unit = block.unit ?? '';
  return /^\p{L}/u.test(unit) ? ` ${unit}` : unit;
}

export function formatReading(block: ChartBlock, value: number): string {
  if (!Number.isFinite(value)) return 'No reading';
  return `${block.format?.prefix ?? ''}${value.toLocaleString('en-US', {
    maximumFractionDigits: block.format?.decimals ?? 0,
    minimumFractionDigits: block.format?.decimals ?? 0,
  })}${readingSuffix(block)}`;
}

/** Compact readings keep fractional precision without padding whole numbers. */
export function formatTrimmedReading(block: ChartBlock, value: number): string {
  if (!Number.isFinite(value)) return 'No reading';
  return `${block.format?.prefix ?? ''}${value.toLocaleString('en-US', {
    maximumFractionDigits: block.format?.decimals ?? 0,
  })}${readingSuffix(block)}`;
}

/** Axis labels use the approved abbreviated scale; readings retain full precision. */
export function formatAxisTick(block: ChartBlock, value: number): string {
  const prefix = block.format?.prefix ?? '';
  const suffix = readingSuffix(block);
  if (prefix && Math.abs(value) >= 1000) return `${prefix}${Number((value / 1000).toPrecision(12)).toLocaleString('en-US', { maximumSignificantDigits: 12 })}k${suffix}`;
  const number = Number(value.toPrecision(12)).toLocaleString('en-US', { maximumSignificantDigits: 12, useGrouping: !!prefix });
  return prefix ? `${prefix}${number}${suffix}` : suffix === '%' ? `${number}%` : number;
}

/** Only retained field declarations establish count semantics; ratios remain fractional. */
export function hasCountAxis(block: ChartBlock, projection?: ChartProjection, range = 'all'): boolean {
  const binding = block.binding;
  if (binding?.kind !== 'dataset' || !('field' in binding.metric) || projection?.kind !== 'category') return false;
  const field = binding.metric.field, selected = categoryRange(projection, range);
  const results = projection.dataset_results.filter(item => item.dataset_id === binding.dataset_id && selected && item.date >= selected.first_date && item.date <= selected.last_date);
  return results.length > 0 && results.every(item => item.output.fields.find(item => item.name === field)?.kind === 'count');
}

export function categoryRange(projection: ChartProjection | undefined, range = 'all'): CategoryChartRange | undefined {
  return projection?.kind === 'category' ? projection.ranges.find(item => item.id === range) ?? projection.ranges.find(item => item.id === 'all') : undefined;
}

export function categoryNumber(value: CategoryChartValue | null | undefined): number | undefined {
  return value?.status === 'value' && value.value !== null && Number.isFinite(value.value) ? value.value : undefined;
}

/** Coverage qualifies comparisons without changing retained quantities. */
export function combinedCoverage(statuses: Array<MeasureValue['coverage_status'] | undefined>): MeasureValue['coverage_status'] {
  return statuses.includes('partial') ? 'partial' : statuses.includes('unknown') ? 'unknown' : statuses.includes('complete') ? 'complete' : 'not_applicable';
}

export function categoryCoverage(summary?: CategoryChartSummary): MeasureValue['coverage_status'] {
  return combinedCoverage([summary?.actual.partial ? 'partial' : summary?.actual.coverage_status, summary?.comparison?.partial ? 'partial' : summary?.comparison?.coverage_status]);
}

export function coverageLabel(status?: MeasureValue['coverage_status']): string {
  return status === 'partial' ? 'Partial reading' : status === 'unknown' ? 'Coverage unavailable' : '';
}

export function seriesKey(series: ChartSeries, index: number): string {
  return series.key ?? (index === 0 ? 'actual' : series.name);
}

export function selectedSeries(block: ChartBlock, range: string, projection?: ChartProjection): ChartSeries[] {
  if (block.binding) return categoryRange(projection, range)?.series.map(item => ({
    key: item.key, name: item.label,
    points: item.points.map(point => ({ label: point.label, value: categoryNumber(point.actual) ?? null })),
  })) ?? [];
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

export function chartData(block: ChartBlock, values: MeasureValue[], range: ChartRangeSelection, projection?: ChartProjection): LineChartDatum[] {
  if (block.binding) {
    const selected = categoryRange(projection, range);
    if (!selected) return [];
    const points = new Map<string, LineChartDatum>();
    const empty = Object.fromEntries(selected.series.map(item => [item.key, undefined]));
    const summaries = new Map(selected.point_summaries.map(item => [item.key, item.summary]));
    const dates = new Map<string, string>();
    for (const series of selected.series) for (const point of series.points) {
      const datum = points.get(point.key) ?? {
        key: point.key, label: point.label,
        axisLabel: selected.axis === 'date' && point.date ? new Date(point.date + 'T12:00:00Z').toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' }) : point.label,
        values: { ...empty },
      };
      datum.values[series.key] = categoryNumber(point.actual);
      const summary = summaries.get(point.key);
      if (summary) datum.values.target = categoryNumber(summary.comparison);
      // Dated summaries compare the whole population, never each category line.
      if (selected.axis === 'category' && series.key === 'actual') datum.comparisons = { actual: summary?.meets_expectation ?? null };
      if (selected.axis === 'date' && point.date) dates.set(point.key, point.date);
      points.set(point.key, datum);
    }
    const data = [...points.values()];
    const first = Date.parse(dates.get(data[0]?.key) ?? ''), last = Date.parse(dates.get(data.at(-1)?.key ?? '') ?? '');
    return data.map(datum => ({ ...datum, ...(selected.axis === 'date' && last > first ? { position: (Date.parse(dates.get(datum.key) ?? '') - first) / (last - first) } : {}) }));
  }
  if (block.expectation) {
    if (projection?.kind !== 'scalar') return [];
    const cutoff = Date.parse(projection.today) - (range === 'week' ? 6 : 29) * 86400000;
    const points = projection.points.filter(point => range !== 'week' && range !== 'month' || point.date <= projection.today && Date.parse(point.date) >= cutoff);
    const first = Date.parse(points[0]?.date ?? ''), last = Date.parse(points.at(-1)?.date ?? '');
    return points.map(point => ({
      key: point.date, label: point.date,
      axisLabel: new Date(point.date + 'T12:00:00Z').toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' }),
      ...(last > first ? { position: (Date.parse(point.date) - first) / (last - first) } : {}),
      values: { actual: point.actual.status === 'value' ? point.actual.value ?? undefined : undefined, target: point.expected.status === 'value' ? point.expected.value ?? undefined : undefined },
      breakBefore: { target: point.break_before },
      comparisons: { actual: coverageLabel(combinedCoverage([point.actual.coverage_status, point.expected.coverage_status])) ? null : point.meets_expectation },
      observation: point.actual, coverage_status: combinedCoverage([point.actual.coverage_status, point.expected.coverage_status]),
      coverage_reasons: [...point.actual.coverage_reasons, ...point.expected.coverage_reasons],
    }));
  }
  const selected = selectedSeries(block, range);
  const target = selectedTarget(block, range);
  const missingCategories = Object.fromEntries((block.measure_id ? [] : selected).map((series, index) => [seriesKey(series, index), undefined]));
  let data: LineChartDatum[];
  if (block.measure_id) {
    const actual = values.filter(v => v.measure_id === block.measure_id).sort((a, b) => a.date.localeCompare(b.date));
    const latest = actual.at(-1)?.date;
    const cutoff = latest ? Date.parse(latest) - (range === 'week' ? 6 : range === 'month' ? 29 : Infinity) * 86400000 : -Infinity;
    data = actual.filter(v => Date.parse(v.date) >= cutoff).map(v => ({
      key: v.date, label: v.date,
      axisLabel: new Date(v.date + 'T12:00:00Z').toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' }),
      values: { actual: v.value ?? undefined }, observation: v, coverage_status: v.coverage_status, coverage_reasons: v.coverage_reasons,
    }));
  } else {
    const points = new Map<string, LineChartDatum>();
    selected.forEach((series, index) => series.points.forEach(p => {
      const d = points.get(p.label) ?? { key: p.label, label: p.axis ?? p.label, axisLabel: p.axis ?? p.label, values: {} };
      d.values[seriesKey(series, index)] = p.value ?? undefined;
      points.set(p.label, d);
    }));
    data = [...points.values()];
  }
  const first = Date.parse(data[0]?.key ?? ''), last = Date.parse(data.at(-1)?.key ?? '');
  return data.map(d => {
    const comparison = block.reference?.length ? referenceAt(block, d.key) : target;
    const actual = d.values.actual;
    const direction = block.direction ?? (block.kind === 'time-budget' ? 'atmost' : 'atleast');
    return {
      ...d,
      ...(Number.isFinite(first) && last > first ? { position: (Date.parse(d.key) - first) / (last - first) } : {}),
      values: { ...missingCategories, ...d.values, ...(comparison === undefined ? {} : { target: comparison }) },
      ...(block.measure_id ? { comparisons: { actual: coverageLabel(d.coverage_status) || actual === undefined || comparison === undefined
        ? null : direction === 'atmost' ? actual <= comparison : actual >= comparison } } : {}),
    };
  });
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
export function allocationSummary(series: ChartSeries[], reading: LineChartDatum | undefined, summary?: CategoryChartSummary): string {
  const values = series.map((item, index) => ({ name: item.name, index, value: reading?.values[seriesKey(item, index)] }));
  if (values.some(item => item.value === undefined)) return summary ? 'Some category readings are unavailable' : 'Current reading unavailable';
  const total = summary ? categoryNumber(summary.actual) : values.reduce((sum, item) => sum + item.value!, 0);
  if (total === undefined) return 'Current reading unavailable';
  if (total <= 0) return '';
  if (summary) return [...values].sort((a, b) => b.value! - a.value! || a.index - b.index).map(item => `${(item.value! / total * 100).toLocaleString('en-US', { maximumFractionDigits: 1 })}% ${item.name.toLowerCase()}`).join(' · ');
  const shares = values.map(item => ({ ...item, exact: item.value! / total * 100, percent: Math.floor(item.value! / total * 100) }));
  const remaining = 100 - shares.reduce((sum, item) => sum + item.percent, 0);
  [...shares].sort((a, b) => (b.exact - b.percent) - (a.exact - a.percent) || a.index - b.index).slice(0, remaining).forEach(item => item.percent++);
  return shares.sort((a, b) => b.exact - a.exact || a.index - b.index).map(item => `${item.percent}% ${item.name.toLowerCase()}`).join(' · ');
}

export interface ChartHeadline { value: number | undefined; target: number | undefined; label: string | undefined; missingCount: number; point?: ScalarChartPoint; categorySummary?: CategoryChartSummary; coverage_status?: MeasureValue['coverage_status']; coverage_reasons?: MeasureValue['coverage_reasons']; observations?: MeasureValue[] }

export function chartHeadline(block: ChartBlock, data: LineChartDatum[], activeKey: string | null = null, projection?: ChartProjection, range = 'all'): ChartHeadline {
  if (block.binding) {
    const selected = categoryRange(projection, range);
    const inspected = activeKey ? selected?.point_summaries.find(item => item.key === activeKey) : undefined;
    const summary = inspected?.summary ?? selected?.summary;
    return {
      value: categoryNumber(summary?.actual), target: categoryNumber(summary?.comparison),
      label: inspected ? data.find(item => item.key === inspected.key)?.label : summary?.date ?? undefined,
      missingCount: summary?.actual.missing_dates.length ?? 0, categorySummary: summary, coverage_status: categoryCoverage(summary),
    };
  }
  if (block.expectation) {
    const scalar = projection?.kind === 'scalar' ? projection : undefined;
    const reading = data.find(item => item.key === (activeKey ?? scalar?.summary_date));
    return { value: reading?.values.actual, target: reading?.values.target, label: reading?.label, missingCount: data.filter(item => item.values.actual === undefined).length, point: scalar?.points.find(point => point.date === reading?.key), coverage_status: reading?.coverage_status, coverage_reasons: reading?.coverage_reasons, observations: reading?.observation ? [reading.observation] : [] };
  }
  const reading = inspectedReading(data, activeKey);
  const primary = block.measure_id ? 'actual' : selectedSeries(block, range)[0] ? seriesKey(selectedSeries(block, range)[0], 0) : 'actual';
  const missingCount = data.filter(d => d.values[primary] === undefined).length;
  if (block.stacked || block.kind === 'allocation') {
    return { value: compositionTotal(data, reading), target: reading?.values.target, label: reading?.label, missingCount, coverage_status: reading?.coverage_status, coverage_reasons: reading?.coverage_reasons, observations: reading?.observation ? [reading.observation] : [] };
  }
  if (block.aggregate === 'sum' && !activeKey) {
    const sum = (key: string) => {
      const known = data.flatMap(d => d.values[key] === undefined ? [] : [d.values[key]!]);
      return known.length ? known.reduce((a, b) => a + b, 0) : undefined;
    };
    return { value: sum(primary), target: sum('target'), label: undefined, missingCount, coverage_status: combinedCoverage([...data.map(item => item.coverage_status), ...(block.measure_id && missingCount ? ['unknown' as const] : [])]), coverage_reasons: [...new Set(data.flatMap(item => item.coverage_reasons ?? []))], observations: data.flatMap(item => item.observation ? [item.observation] : []) };
  }
  const value = reading?.values[primary];
  return { value: (block.kind === 'progress' || block.kind === 'time-budget') && value !== undefined && value < 0 ? undefined : value, target: reading?.values.target, label: reading?.label, missingCount, coverage_status: reading?.coverage_status, coverage_reasons: reading?.coverage_reasons, observations: reading?.observation ? [reading.observation] : [] };
}

export function chartTone(block: ChartBlock, value: number | undefined, target: number | undefined, point?: ScalarChartPoint, categorySummary?: CategoryChartSummary, coverage_status?: MeasureValue['coverage_status']): GoalResponse['status'] {
  if (coverageLabel(combinedCoverage([coverage_status, point?.actual.coverage_status, point?.expected.coverage_status, categoryCoverage(categorySummary)]))) return 'neutral';
  if (block.binding) return categorySummary?.meets_expectation == null || categorySummary.actual.partial || categorySummary.comparison?.partial ? 'neutral' : categorySummary.meets_expectation ? 'good' : block.below_target_tone ?? 'bad';
  if (block.expectation) return point?.meets_expectation == null ? 'neutral' : point.meets_expectation ? 'good' : block.below_target_tone ?? 'bad';
  if (value === undefined || target === undefined) return 'neutral';
  const good = (block.direction ?? (block.kind === 'time-budget' ? 'atmost' : 'atleast')) === 'atmost' ? value <= target : value >= target;
  return good ? 'good' : block.below_target_tone ?? 'bad';
}

export function comparisonText(block: ChartBlock, value: number | undefined, target: number | undefined, point?: ScalarChartPoint, categorySummary?: CategoryChartSummary, coverage_status?: MeasureValue['coverage_status']): string {
  const coverage = combinedCoverage([coverage_status, point?.actual.coverage_status, point?.expected.coverage_status, categoryCoverage(categorySummary)]);
  if (block.binding && categorySummary?.difference == null) return coverage === 'unknown' ? 'Coverage unavailable' : categorySummary?.actual.partial || categorySummary?.comparison?.partial ? 'Partial total' : coverageLabel(coverage);
  if (block.expectation && point?.difference == null) return '';
  if (value === undefined || target === undefined) return '';
  const signedDifference = block.binding ? categorySummary!.difference! : block.expectation ? point!.difference! : value - target;
  const difference = Math.abs(signedDifference);
  const decimals = block.comparison_decimals ?? block.format?.decimals ?? 0;
  const formatDifference = (quantity: number) => block.difference_unit === 'pp'
    ? `${quantity.toLocaleString('en-US', { minimumFractionDigits: decimals, maximumFractionDigits: decimals })} pp`
    : formatReading({ ...block, format: { ...block.format, decimals } }, quantity);
  if (coverageLabel(coverage) || block.binding && categorySummary!.meets_expectation == null) return `${coverage === 'unknown' ? 'Difference (coverage unavailable)' : 'Partial difference'}: ${formatDifference(signedDifference)}`;
  if (block.expectation && point?.meets_expectation == null) return '';
  const amount = formatDifference(difference);
  if (block.kind === 'progress') return signedDifference === 0 ? 'Target reached' : amount + (signedDifference < 0 ? ' remaining' : ' beyond target');
  if (block.kind === 'time-budget') return signedDifference === 0 ? 'Budget used' : amount + (signedDifference < 0 ? ' remaining' : ' over budget');
  const label = block.target_label ?? (block.direction === 'atmost' ? 'limit' : 'target');
  if ((block.expectation || block.binding ? signedDifference === 0 : difference < .0001) && block.kind !== 'bar' && !block.aggregate) return `On ${label}`;
  if (signedDifference < 0 && block.direction === 'atmost' && block.within_limit_label === 'remaining') return `${amount} remaining`;
  if (signedDifference < 0 && block.direction !== 'atmost' && label === 'target' && block.shortfall_label === 'to target') return `${amount} to target`;
  return `${amount} ${block.direction === 'atmost' ? (signedDifference <= 0 ? 'under' : 'over') : (signedDifference >= 0 ? 'above' : 'below')} ${label}`;
}

/** Ring quantities come from the selected backend summary, not plotted sums. */
export function allocationData(data: LineChartDatum[], series: ChartSeries[], selected?: CategoryChartRange, activeKey: string | null = null): { reading: LineChartDatum | undefined; series: ChartSeries[]; total: number | undefined; summary?: CategoryChartSummary } {
  if (!selected) {
    const reading = inspectedReading(data, activeKey);
    return { reading, series, total: compositionTotal(data, reading) };
  }
  const inspected = activeKey ? selected.point_summaries.find(item => item.key === activeKey) : undefined;
  if (inspected) return { reading: data.find(item => item.key === inspected.key), series, total: categoryNumber(inspected.summary.actual), summary: inspected.summary };
  const members = selected.series.filter(item => item.summary !== null);
  return {
    reading: { key: 'range', label: selected.label, values: Object.fromEntries(members.map(item => [item.key, categoryNumber(item.summary)])) },
    series: series.filter(item => members.some(member => member.key === item.key)),
    total: categoryNumber(selected.summary.actual), summary: selected.summary,
  };
}

export function categoryDetails(summary?: CategoryChartSummary, projection?: ChartProjection, range = 'all'): string[] {
  if (!summary) return [];
  const values = [summary.actual, ...(summary.comparison ? [summary.comparison] : [])];
  const missing = [...new Set(values.flatMap(item => item.missing_dates))];
  const details = values.flatMap(item => item.reasons);
  if (missing.length) details.push(`${missing.length} missing ${missing.length === 1 ? 'date' : 'dates'}: ${missing.slice(0, 3).join(', ')}${missing.length > 3 ? '…' : ''}.`);
  if (projection?.kind === 'category' && (values.some(item => item.partial) || summary.date !== null)) {
    const selected = categoryRange(projection, range);
    const evidence = projection.dataset_results.filter(item => summary.date === null
      ? selected && item.date >= selected.first_date && item.date <= selected.last_date
      : item.date === summary.date).at(-1);
    if (evidence) details.push(`${summary.date === null ? 'Latest retained query' : 'Retained query'} for ${evidence.date} has ${evidence.query_start_at === null ? 'no supplied start restriction' : `a start at ${new Date(evidence.query_start_at * 1000).toISOString()}`} and ends at ${new Date(evidence.query_end_at * 1000).toISOString()}.`);
  }
  return [...new Set(details)];
}

export function observationDetails(headline: ChartHeadline, projection?: ChartProjection, range = 'all'): string[] {
  const details = [...categoryDetails(headline.categorySummary, projection, range), ...(headline.coverage_reasons ?? [])];
  const selected = categoryRange(projection, range);
  const observations = headline.observations ?? (projection?.kind === 'category' ? projection.measure_values.filter(item => headline.categorySummary?.date != null
    ? item.date === headline.categorySummary.date : !selected || item.date >= selected.first_date && item.date <= selected.last_date) : []);
  const inputs = [...observations.flatMap(observation => [observation, ...observation.inputs]), ...(headline.point?.expected.inputs ?? [])];
  for (const input of inputs) {
    details.push(...(input.coverage_reasons ?? []));
    if (input.limitation) details.push(input.limitation);
    if (input.query_start_at != null || input.query_end_at != null) details.push(`Retained query for ${input.date} starts at ${input.query_start_at == null ? 'an unavailable bound' : new Date(input.query_start_at * 1000).toISOString()} and ends at ${input.query_end_at == null ? 'an unavailable bound' : new Date(input.query_end_at * 1000).toISOString()}.`);
  }
  return [...new Set(details)];
}

export function missingReadingText(data: LineChartDatum[], label?: string): string {
  if (!data.some(d => Object.entries(d.values).some(([key, value]) => key !== 'target' && value !== undefined))) return 'No readings yet';
  return label ? `No reading for ${label}` : 'No readings in this range';
}
