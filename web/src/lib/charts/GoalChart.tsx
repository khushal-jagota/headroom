import React, { useState, useMemo, useRef, useLayoutEffect } from 'react';
import { motion, useReducedMotion } from 'motion/react';
import type { ChartBlock, MeasureValue, ChartProjection, GoalResponse } from '../goals/contracts';
import { LineChart, type LineChartDatum, type LineChartSeries } from './components/line-chart-integrated';
import { AnimatedCounter } from './components/animated-counter';
import { CompletionReading } from './components/completion-reading';
import { SupportingMetric } from './components/supporting-metric';
import { Gauge } from './components/gauge';
import { ProgressCard, TimeBudgetCard, AllocationCard, seriesColor } from './components/goal-indicators';
import { Bars } from './components/bar-chart';
import { motionTokens } from './lib/motion-tokens';
import { chartData, chartHeadline, chartTone, comparisonText, formatReading, formatTrimmedReading, formatAxisTick, hasCountAxis, missingReadingText, readChartPreference, selectedSeries, selectedTarget, selectedDomain, allocationSummary, categoryRange, observationDetails, coverageLabel, seriesKey, allocationData } from './presentation';
import './theme.css';

const colors = { good: 'var(--accent-done)', bad: 'var(--accent-error)', attention: 'var(--accent-attention)', neutral: 'var(--text-muted)' };
const C = { green: colors.good, blue: 'var(--accent-bright)', reference: 'var(--chart-reference)' };
export type GoalChartProps = { block: ChartBlock; values: MeasureValue[]; projection?: ChartProjection; narrow: boolean; preferenceKey: string; completed?: boolean; status?: GoalResponse['status']; compact?: boolean; integrated?: boolean };

export function Range({ value, onChange, id, block, projection }: { value: string; onChange: (v: string) => void; id: string; block: ChartBlock; projection?: ChartProjection }) {
  const reduce = useReducedMotion();
  const choices = (block.binding && projection?.kind === 'category' ? projection.ranges : block.ranges)?.map(r => [r.id, r.label]) ?? [['week', 'Week'], ['month', 'Month'], ['all', 'All time']];
  return <div className="ranges" role="group" aria-label="Time range">{choices.map(([key, label]) => <button type="button" key={key} aria-pressed={value === key} onClick={() => onChange(key)}>
    {key === value && <motion.span className="range-selection" layoutId={'range-' + id} transition={reduce ? { duration: 0 } : motionTokens.spring.snappy} />}
    <span>{label}</span>
  </button>)}</div>;
}

function Comparison({ block, data, headline, supporting = false }: { block: ChartBlock; data: LineChartDatum[]; headline: ReturnType<typeof chartHeadline>; supporting?: boolean }) {
  const { value, target, label, missingCount } = headline;
  const text = value === undefined ? supporting ? 'Current reading unavailable' : missingReadingText(data, label) : block.expectation && target === undefined ? 'Expected reading unavailable' : comparisonText(block, value, target, headline.point, headline.categorySummary, headline.coverage_status);
  const tone = !coverageLabel(headline.coverage_status) && !block.binding && block.kind === 'progress' && value !== undefined && target !== undefined && value < target ? C.blue : colors[chartTone(block, value, target, headline.point, headline.categorySummary, headline.coverage_status)];
  return <div className="comparison" style={{ color: tone }}>{value !== undefined && coverageLabel(headline.coverage_status) && text !== coverageLabel(headline.coverage_status) && <span>{coverageLabel(headline.coverage_status)}{text ? ' · ' : ''}</span>}{text}
    {block.aggregate === 'sum' && !label && value !== undefined && missingCount > 0 && <span className="partial-reading"> · {missingCount} missing {missingCount === 1 ? 'reading' : 'readings'}</span>}
  </div>;
}

function CategoryNotes({ headline, projection, range }: { headline: ReturnType<typeof chartHeadline>; projection?: ChartProjection; range: string }) {
  const details = observationDetails(headline, projection, range);
  return details.length ? <details className="observation-details caption"><summary>Observation details</summary><ul>{details.map(detail => <li key={detail}>{detail}</li>)}</ul></details> : null;
}

export function GoalChart({ block, values, projection, narrow, preferenceKey, completed = false, status = 'neutral', compact: small = false, integrated = false }: GoalChartProps) {
  const [preferences, setPreferences] = useState(() => {
    const saved = readChartPreference(preferenceKey);
    return block.ranges?.length && !block.ranges.some(r => r.id === saved.range) ? { ...saved, range: block.ranges[0].id } : saved;
  });
  const [activeKey, setActiveKey] = useState<string | null>(null);
  const setActive = (datum: LineChartDatum | null) => setActiveKey(datum?.key ?? null);
  const root = useRef<HTMLElement>(null), focusInside = useRef(false);
  const compact = small || block.placement === 'supporting', foldable = block.placement === 'supporting' && narrow;
  const data = useMemo(() => chartData(block, values, preferences.range, projection), [block, values, preferences.range, projection]);
  const authoredSeries = useMemo(() => selectedSeries(block, preferences.range, projection), [block, preferences.range, projection]);
  if (activeKey !== null && !data.some(item => item.key === activeKey)) setActiveKey(null);
  const selectedRange = categoryRange(projection, preferences.range);
  const headline = chartHeadline(block, data, activeKey, projection, preferences.range), summary = chartHeadline(block, data, null, projection, preferences.range);
  const { value, target } = headline;
  const displayBlock = useMemo(() => ({ ...block, target: block.binding || block.measure_id || block.expectation ? target : selectedTarget(block, preferences.range), domain: selectedDomain(block, preferences.range) }), [block, preferences.range, target]);
  const title = block.title ?? 'Reading';
  const hasRange = !!block.binding || !!block.ranges?.length || !!block.measure_id && (block.kind === 'line' || block.kind === 'bar') || (block.kind === 'line' || block.kind === 'bar') && !block.stacked;
  const indicator = block.kind === 'allocation' || block.kind === 'time-budget';

  function preference(patch: Partial<typeof preferences>) {
    setActive(null);
    setPreferences(current => {
      const next = { ...current, ...patch };
      try { sessionStorage.setItem(preferenceKey, JSON.stringify(next)); } catch { /* Storage may be unavailable. */ }
      return next;
    });
  }
  const trackFocus = {
    onFocusCapture: () => { focusInside.current = true; },
    onBlurCapture: (event: React.FocusEvent<HTMLElement>) => {
      if (event.relatedTarget && !root.current?.contains(event.relatedTarget as Node)) focusInside.current = false;
    },
  };
  useLayoutEffect(() => {
    if (foldable && !preferences.expanded && focusInside.current) root.current?.querySelector<HTMLButtonElement>('.support-summary')?.focus();
  }, [foldable, preferences.expanded]);

  const badColor = colors[block.below_target_tone ?? 'bad'];
  const latestReading = block.expectation ? undefined : data.at(-1)?.values;
  const datedComparisons = !!block.expectation || !!block.measure_id && data.some(item => !!coverageLabel(item.coverage_status));
  const actualKey = block.measure_id ? 'actual' : authoredSeries[0] ? seriesKey(authoredSeries[0], 0) : 'actual';
  const categorySeries: LineChartSeries[] = authoredSeries.map((item, index) => ({ key: seriesKey(item, index), label: item.name, color: seriesColor(item, index), area: index === 0 }));
  const series: LineChartSeries[] = [
    ...(block.binding ? categorySeries : [
    { key: actualKey, label: authoredSeries[0]?.name ?? 'Current', color: block.expectation ? colors[chartTone(block, summary.value, summary.target, summary.point, summary.categorySummary, summary.coverage_status)] : latestReading?.target === undefined ? C.blue : latestReading[actualKey] === undefined ? badColor : colors[chartTone(block, latestReading[actualKey], latestReading.target, undefined, undefined, data.at(-1)?.coverage_status)], area: true,
      datedComparisons, neutralColor: colors.neutral, compareTo: !datedComparisons && data.some(d => d.values.target !== undefined) ? 'target' : undefined, higherIsBetter: block.direction !== 'atmost', goodColor: C.green, badColor },
    ...authoredSeries.slice(1).map((item, index) => ({ key: seriesKey(item, index + 1), label: item.name, area: false, color: seriesColor(item, index + 1) })),
    ]),
    ...(data.some(d => d.values.target !== undefined) ? [{ key: 'target', label: block.expectation ? 'Expected' : block.binding || block.reference?.length || block.aggregate === 'sum' ? 'Target' : `${formatTrimmedReading(block, displayBlock.target!)} ${block.target_label ?? (block.direction === 'atmost' ? 'limit' : 'target')}`, color: C.reference, dashed: true, area: false, curve: block.expectation ? 'linear' as const : undefined }] : []),
  ];
  const counter = (number: number | undefined) => number === undefined ? <span className="missing-value">—</span> : <AnimatedCounter value={number} prefix={block.format?.prefix} suffix={block.format?.suffix ?? block.unit} decimals={block.format?.decimals ?? 0} />;
  const range = <Range value={selectedRange?.id ?? preferences.range} onChange={range => preference({ range })} id={preferenceKey} block={block} projection={projection} />;
  let chart: React.ReactNode;
  if (block.kind === 'allocation') chart = <AllocationCard block={block} data={data} series={authoredSeries} range={block.binding ? selectedRange : undefined} activeKey={activeKey} />;
  else if (block.kind === 'time-budget') chart = <TimeBudgetCard block={displayBlock} value={value} summary={headline.categorySummary} coverage_status={headline.coverage_status} />;
  else if (block.kind === 'progress') chart = <ProgressCard block={displayBlock} value={value} summary={headline.categorySummary} coverage_status={headline.coverage_status} />;
  else if (block.kind === 'gauge') chart = value === undefined ? <p className="chart-empty-value">{missingReadingText(data, headline.label)}</p> : <Gauge value={value} tone={coverageLabel(headline.coverage_status) ? 'neutral' : undefined} min={displayBlock.domain?.[0]} max={displayBlock.domain?.[1]} formatValue={v => formatTrimmedReading(block, v)} unit={block.format?.suffix ?? block.unit ?? ''} label={title}
    target={displayBlock.target === undefined ? undefined : { value: displayBlock.target, label: `${formatTrimmedReading(block, displayBlock.target)} ${block.target_label ?? 'target'}` }}
    thresholds={!!coverageLabel(headline.coverage_status) || displayBlock.target === undefined || block.binding && headline.categorySummary?.meets_expectation == null ? undefined : [
      { from: Math.min(0, value), tone: block.direction === 'atmost' ? 'success' : 'danger', label: block.direction === 'atmost' ? 'Within limit' : 'Below target' },
      { from: displayBlock.target! + (block.direction === 'atmost' ? .00001 : 0), tone: block.direction === 'atmost' ? 'danger' : 'success', label: block.direction === 'atmost' ? 'Above limit' : 'On target' },
    ]} />;
  else if (block.kind === 'bar') chart = <Bars block={displayBlock} data={data} series={authoredSeries} compact={compact} activeKey={activeKey} onActive={setActive} projection={projection} range={preferences.range} />;
  else if (block.kind === 'number') chart = null;
  else chart = <LineChart data={data} series={series} label={title} height={block.plot_height ?? (compact ? 176 : 216)} domain={displayBlock.domain} integerTicks={hasCountAxis(block, projection, preferences.range)} formatValue={v => foldable ? formatTrimmedReading(block, v) : formatReading(block, v)} formatTick={v => formatAxisTick(block, v)} onActiveChange={(_, datum) => setActive(datum)} emptyLabel="No readings yet" />;
  const plot = <>{chart}{compact && hasRange && <div className="chart-range-footer">{range}</div>}</>;

  if (block.kind === 'binary') return <article className={`metric-card indicator-card completion-card ${integrated ? 'integrated-card' : ''}`}>{block.title && <div className="metric-top"><h2>{block.title}</h2></div>}<CompletionReading done={completed} tone={colors[status]} context={completed ? 'Completed' : status === 'bad' ? 'Overdue' : status === 'attention' ? 'Due today' : 'Not done'} /></article>;
  if (foldable) {
    const visible = preferences.expanded ? headline : summary;
    const allocation = allocationData(data, authoredSeries, block.binding ? selectedRange : undefined, preferences.expanded ? activeKey : null);
    return <section ref={root} {...trackFocus}><SupportingMetric title={title} reading={block.kind === 'allocation' && visible.value !== undefined ? formatReading(block, visible.value) : counter(visible.value)}
      status={<>{block.kind === 'allocation' && visible.value !== undefined ? <><span style={{ color: C.blue }}>{allocationSummary(allocation.series, allocation.reading, allocation.summary)}</span>{(!!coverageLabel(visible.coverage_status)) && <Comparison block={block} data={data} headline={visible} supporting />}</> : <Comparison block={block} data={data} headline={visible} supporting />}</>}
      open={preferences.expanded} onToggle={() => preference({ expanded: !preferences.expanded })}>{plot}<CategoryNotes headline={visible} projection={projection} range={preferences.range} /></SupportingMetric></section>;
  }
  return <article ref={root} {...trackFocus} className={`metric-card ${integrated ? 'integrated-card ' : ''}${compact ? 'compact ' : ''}${block.emphasis === 'hero' ? 'hero-card ' : ''}${block.kind === 'gauge' ? 'gauge-card ' : ''}${indicator || block.kind === 'progress' ? `indicator-card ${block.kind}-card` : ''}`}>
    <div className="metric-top"><h2>{title}</h2>{!compact && hasRange && range}</div>
    {block.kind !== 'gauge' && !indicator && <div className="headline">
      {block.kind === 'progress' && <div className="caption">{coverageLabel(headline.coverage_status) || 'Completed'}</div>}
      <div className={block.kind === 'progress' ? 'meter-value' : 'metric-value'} data-current={value ?? 'missing'}>{counter(value)}{block.kind === 'progress' && displayBlock.target !== undefined && displayBlock.target > 0 && <span className="meter-denominator">of {formatReading(block, displayBlock.target)}</span>}</div>
      {block.stacked && !coverageLabel(headline.coverage_status) ? <div className="comparison composition-summary">{authoredSeries.length} {block.total_label ?? 'categories'}</div> : <Comparison block={block} data={data} headline={headline} />}
      <CategoryNotes headline={headline} projection={projection} range={preferences.range} />
    </div>}
    {plot}
    {(block.binding || !!coverageLabel(headline.coverage_status)) && (indicator || block.kind === 'gauge') && <><Comparison block={block} data={data} headline={headline} /><CategoryNotes headline={headline} projection={projection} range={preferences.range} /></>}
    {block.caption && <div className="caption">{block.caption}</div>}
  </article>;
}
