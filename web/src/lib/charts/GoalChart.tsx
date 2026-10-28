import React, { useState, useMemo, useRef, useLayoutEffect } from 'react';
import { motion, useReducedMotion } from 'motion/react';
import type { ChartBlock, MeasureValue, GoalResponse } from '../goals/contracts';
import { LineChart, type LineChartDatum, type LineChartSeries } from './components/line-chart-integrated';
import { AnimatedCounter } from './components/animated-counter';
import { CompletionReading } from './components/completion-reading';
import { SupportingMetric } from './components/supporting-metric';
import { Gauge } from './components/gauge';
import { ProgressCard, TimeBudgetCard, AllocationCard, seriesColor } from './components/goal-indicators';
import { Bars } from './components/bar-chart';
import { motionTokens } from './lib/motion-tokens';
import { chartData, chartHeadline, chartTone, comparisonText, formatReading, missingReadingText, readChartPreference, selectedSeries, selectedTarget } from './presentation';
import './theme.css';

const colors = { good: 'var(--accent-done)', bad: 'var(--accent-error)', attention: 'var(--accent-attention)', neutral: 'var(--text-muted)' };
const C = { green: colors.good, blue: 'var(--accent-bright)', reference: 'var(--text-faintest)' };
export type GoalChartProps = { block: ChartBlock; values: MeasureValue[]; narrow: boolean; preferenceKey: string; completed?: boolean; status?: GoalResponse['status']; compact?: boolean };

function Range({ value, onChange, id, block }: { value: string; onChange: (v: string) => void; id: string; block: ChartBlock }) {
  const reduce = useReducedMotion();
  const choices = block.ranges?.map(r => [r.id, r.label]) ?? [['week', 'Week'], ['month', 'Month'], ['all', 'All time']];
  return <div className="ranges" role="group" aria-label="Time range">{choices.map(([key, label]) => <button type="button" key={key} aria-pressed={value === key} onClick={() => onChange(key)}>
    {key === value && <motion.span className="range-selection" layoutId={'range-' + id} transition={reduce ? { duration: 0 } : motionTokens.spring.snappy} />}
    <span>{label}</span>
  </button>)}</div>;
}

function Comparison({ block, data, headline }: { block: ChartBlock; data: LineChartDatum[]; headline: ReturnType<typeof chartHeadline> }) {
  const { value, target, label, missingCount } = headline;
  const text = value === undefined ? missingReadingText(data, label) : comparisonText(block, value, target);
  const tone = block.kind === 'progress' && value !== undefined && target !== undefined && value < target ? C.blue : colors[chartTone(block, value, target)];
  return <div className="comparison" style={{ color: tone }}>{text}
    {block.aggregate === 'sum' && !label && value !== undefined && missingCount > 0 && <span className="partial-reading"> · {missingCount} missing {missingCount === 1 ? 'reading' : 'readings'}</span>}
  </div>;
}

export function GoalChart({ block, values, narrow, preferenceKey, completed = false, status = 'neutral', compact: small = false }: GoalChartProps) {
  const [preferences, setPreferences] = useState(() => {
    const saved = readChartPreference(preferenceKey);
    return block.ranges?.length && !block.ranges.some(r => r.id === saved.range) ? { ...saved, range: block.ranges[0].id } : saved;
  });
  const [activeKey, setActiveKey] = useState<string | null>(null);
  const setActive = (datum: LineChartDatum | null) => setActiveKey(datum?.key ?? null);
  const root = useRef<HTMLElement>(null), focusInside = useRef(false);
  const compact = small || narrow, foldable = block.placement === 'supporting' && narrow;
  const data = useMemo(() => chartData(block, values, preferences.range), [block, values, preferences.range]);
  const authoredSeries = useMemo(() => selectedSeries(block, preferences.range), [block, preferences.range]);
  const headline = chartHeadline(block, data, activeKey), summary = chartHeadline(block, data);
  const { value, target } = headline;
  const displayBlock = { ...block, target: selectedTarget(block, preferences.range) };
  const title = block.title ?? 'Reading';
  const hasRange = !!block.ranges?.length || !!block.measure_id && (block.kind === 'line' || block.kind === 'bar') || (block.kind === 'line' || block.kind === 'bar') && !block.stacked;
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
  const series: LineChartSeries[] = [
    { key: 'actual', label: authoredSeries[0]?.name ?? 'Current', color: target === undefined ? C.blue : colors[chartTone(block, value, target)], area: true,
      compareTo: data.some(d => d.values.target !== undefined) ? 'target' : undefined, higherIsBetter: block.direction !== 'atmost', goodColor: C.green, badColor },
    ...authoredSeries.slice(1).map((item, index) => ({ key: item.name, label: item.name, area: false, color: seriesColor(item, index + 1) })),
    ...(data.some(d => d.values.target !== undefined) ? [{ key: 'target', label: block.reference?.length ? 'Target path' : `${formatReading(block, displayBlock.target!)} ${block.target_label ?? (block.direction === 'atmost' ? 'limit' : 'target')}`, color: C.reference, dashed: true, area: false }] : []),
  ];
  const counter = (number: number | undefined) => number === undefined ? <span className="missing-value">—</span> : <AnimatedCounter value={number} prefix={block.format?.prefix} suffix={block.format?.suffix ?? block.unit} decimals={block.format?.decimals ?? 0} />;
  const range = <Range value={preferences.range} onChange={range => preference({ range })} id={preferenceKey} block={block} />;
  let chart: React.ReactNode;
  if (block.kind === 'allocation') chart = <AllocationCard block={block} data={data} series={authoredSeries} />;
  else if (block.kind === 'time-budget') chart = <TimeBudgetCard block={displayBlock} value={value} />;
  else if (block.kind === 'progress') chart = <ProgressCard block={displayBlock} value={value} />;
  else if (block.kind === 'gauge') chart = value === undefined ? <p className="chart-empty-value">{missingReadingText(data, headline.label)}</p> : <Gauge value={value} min={block.domain?.[0]} max={block.domain?.[1]} formatValue={v => formatReading(block, v)} label={title}
    target={displayBlock.target === undefined ? undefined : { value: displayBlock.target, label: `${formatReading(block, displayBlock.target)} ${block.target_label ?? 'target'}` }}
    thresholds={displayBlock.target === undefined ? undefined : [
      { from: Math.min(0, value), tone: block.direction === 'atmost' ? 'success' : 'danger', label: block.direction === 'atmost' ? 'Within limit' : 'Below target' },
      { from: displayBlock.target! + (block.direction === 'atmost' ? .00001 : 0), tone: block.direction === 'atmost' ? 'danger' : 'success', label: block.direction === 'atmost' ? 'Above limit' : 'On target' },
    ]} />;
  else if (block.kind === 'bar') chart = <Bars block={block} data={data} series={authoredSeries} compact={compact} activeKey={activeKey} onActive={setActive} />;
  else if (block.kind === 'number') chart = null;
  else chart = <LineChart data={data} series={series} label={title} height={compact ? 176 : 260} domain={block.domain} formatValue={v => formatReading(block, v)} formatTick={v => formatReading(block, v)} onActiveChange={(_, datum) => setActive(datum)} legend={series.length > 2} emptyLabel="No readings yet" />;
  const plot = <>{chart}{compact && hasRange && <div className="chart-range-footer">{range}</div>}</>;

  if (block.kind === 'binary') return <CompletionReading className="integrated-completion" done={completed} tone={colors[status]} context={completed ? 'Completed' : status === 'bad' ? 'Overdue' : status === 'attention' ? 'Due today' : 'Not done'} />;
  if (foldable) {
    const visible = preferences.expanded ? headline : summary;
    return <section ref={root} {...trackFocus}><SupportingMetric title={title} reading={counter(visible.value)}
      status={block.kind === 'allocation' && visible.value !== undefined ? <span style={{ color: C.blue }}>{authoredSeries.map((item, index) => {
        const amount = data.at(-1)?.values[index === 0 ? 'actual' : item.name];
        return `${visible.value && amount !== undefined ? Math.round(amount / visible.value * 100) : 0}% ${item.name.toLowerCase()}`;
      }).join(' · ')}</span> : <Comparison block={block} data={data} headline={visible} />}
      open={preferences.expanded} onToggle={() => preference({ expanded: !preferences.expanded })}>{plot}</SupportingMetric></section>;
  }
  return <article ref={root} {...trackFocus} className={`metric-card integrated-card ${compact ? 'compact ' : ''}${block.kind === 'gauge' ? 'gauge-card' : ''}`}>
    <div className="metric-top"><h2>{title}</h2>{!compact && hasRange && range}</div>
    {block.kind !== 'gauge' && !indicator && <div className="headline">
      {block.kind === 'progress' && <div className="caption">Completed</div>}
      <div className={block.kind === 'progress' ? 'meter-value' : 'metric-value'} data-current={value ?? 'missing'}>{counter(value)}{block.kind === 'progress' && <span className="meter-denominator">of {formatReading(block, displayBlock.target!)}</span>}</div>
      {block.stacked ? <div className="comparison">{authoredSeries.length} {block.total_label ?? 'categories'}</div> : <Comparison block={block} data={data} headline={headline} />}
    </div>}
    {plot}
    {block.caption && <div className="caption">{block.caption}</div>}
  </article>;
}
