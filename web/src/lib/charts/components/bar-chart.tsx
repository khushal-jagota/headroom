/** Production adapter of the approved app.tsx BarCard (period and composition). */
import React, { useEffect, useRef } from 'react';
import { useReducedMotion } from 'motion/react';
import { Chart, registerables, type ChartDataset } from 'chart.js';
import type { ChartBlock, ChartSeries, ChartProjection } from '../../goals/contracts';
import type { LineChartDatum } from './line-chart-integrated';
import { chartHeadline, chartTone, formatReading, formatAxisTick, hasCountAxis, categoryRange, seriesKey } from '../presentation';
import { motionTokens } from '../lib/chart-motion';
import { seriesColor } from './goal-indicators';

Chart.register(...registerables);

export function Bars({ block, data, series, compact, activeKey, onActive, projection, range = 'all' }: {
  block: ChartBlock; data: LineChartDatum[]; series: ChartSeries[]; compact: boolean; activeKey: string | null; onActive: (d: LineChartDatum | null) => void; projection?: ChartProjection; range?: string;
}) {
  const ref = useRef<HTMLCanvasElement>(null), chart = useRef<Chart<'bar' | 'line'> | null>(null);
  const callback = useRef(onActive); callback.current = onActive;
  const found = data.findIndex(datum => datum.key === activeKey);
  const index = found < 0 ? null : found;
  const reduce = useReducedMotion();
  const plotted = series.length ? series : [{ name: block.title ?? 'Current', points: [] }];
  const latest = data.at(-1)?.values;
  const selectedRange = categoryRange(projection, range);
  const summaries = new Map(selectedRange?.point_summaries.map(item => [item.key, item.summary]));
  const currentTone = chartTone(block, latest?.actual, latest?.target, undefined, summaries.get(data.at(-1)?.key ?? ''), data.at(-1)?.coverage_status);
  const latestSummary = summaries.get(data.at(-1)?.key ?? '');
  const unknownVerdict = (summary: typeof latestSummary) => !!block.binding && !!summary && summary.comparison !== null && summary.meets_expectation === null;
  const currentColor = latest?.actual === undefined ? 'var(--chart-muted)' : unknownVerdict(latestSummary) ? 'var(--chart-muted)' : latest.target === undefined ? 'var(--accent-bright)'
    : { good: 'var(--accent-done)', bad: 'var(--accent-error)', attention: 'var(--accent-attention)', neutral: 'var(--chart-muted)' }[currentTone];
  const entries = plotted.map((item, i) => ({ key: block.measure_id && i === 0 ? 'actual' : seriesKey(item, i), label: item.name, color: block.stacked ? seriesColor(item, i, 'composition') : i === 0 ? currentColor : seriesColor(item, i, 'composition') }));
  const topStackKey = entries.at(-1)?.key;
  if (data.some(d => d.values.target !== undefined)) entries.push({ key: 'target', label: block.target_label ?? 'Target', color: 'var(--chart-reference)' });

  useEffect(() => {
    if (!ref.current) return;
    const tokens = getComputedStyle(ref.current), color = (token: string) => tokens.getPropertyValue(token).trim();
    const resolve = (css: string) => css.replace(/var\((--[a-z-]+)\)/g, (_, name: string) => color(name));
    const datasets: ChartDataset<'bar' | 'line', (number | null)[]>[] = entries.map(entry => entry.key === 'target' ? {
      type: 'line', label: entry.label, data: data.map(d => d.values.target ?? null),
      borderColor: color('--chart-reference'), borderDash: [4, 5], borderWidth: 1.5, pointRadius: 0,
    } : {
      type: 'bar', label: entry.label, data: data.map(d => d.values[entry.key] ?? null),
      backgroundColor: block.stacked || entry.key !== 'actual' ? resolve(entry.color) : data.map(d => {
        if (unknownVerdict(summaries.get(d.key))) return color('--chart-muted');
        if (d.values.target === undefined) return color('--accent-bright');
        const point = projection?.kind === 'scalar' ? projection.points.find(item => item.date === d.key) : undefined;
        const tone = chartTone(block, d.values.actual, d.values.target, point, summaries.get(d.key), d.coverage_status);
        return color(tone === 'good' ? '--accent-done' : tone === 'neutral' ? '--chart-muted' : tone === 'attention' ? '--accent-attention' : '--accent-error');
      }),
      borderRadius: block.stacked ? (entry.key === topStackKey ? { topLeft: 4, topRight: 4 } : 0) : 4,
      maxBarThickness: 40, ...(block.stacked ? { stack: 'composition' } : {}),
    });
    const configuration = {
      type: 'bar', data: { labels: data.map(d => d.axisLabel), datasets },
      options: {
        responsive: true, maintainAspectRatio: false,
        animation: reduce ? false : { duration: motionTokens.duration.bar * 1000, easing: 'easeInOutCubic' },
        interaction: { mode: 'index', intersect: false },
        plugins: {
          legend: { display: false },
          tooltip: { backgroundColor: color('--chart-surface-raised'), padding: 12, cornerRadius: 12,
            callbacks: { label: ctx => `${ctx.dataset.label}: ${ctx.parsed.y === null ? 'No reading' : formatReading(block, ctx.parsed.y)}` } },
        },
        scales: {
          x: { stacked: block.stacked, border: { display: false }, grid: { display: false }, ticks: { color: color('--chart-muted'), font: { size: 11 }, maxRotation: 0, maxTicksLimit: 4 } },
          y: { stacked: block.stacked, position: 'right', beginAtZero: true, min: block.domain?.[0], max: block.domain?.[1], border: { display: false }, grid: { color: color('--chart-grid') }, ticks: { color: color('--chart-muted'), font: { size: 11 }, maxTicksLimit: 5, precision: hasCountAxis(block, projection, range) ? 0 : undefined, callback: v => formatAxisTick(block, Number(v)) } },
        },
        onHover: (_, elements) => { const i = elements[0]?.index ?? null; callback.current(i === null ? null : data[i]); },
      },
    } satisfies import('chart.js').ChartConfiguration<'bar' | 'line'>;
    if (chart.current) {
      chart.current.data = configuration.data;
      chart.current.options = configuration.options;
      chart.current.update();
    } else chart.current = new Chart<'bar' | 'line'>(ref.current, configuration);
  }, [data, block, series, reduce, projection, range]);

  useEffect(() => () => { chart.current?.destroy(); chart.current = null; }, []);

  function inspect(i: number | null) {
    callback.current(i === null ? null : data[i]);
    const c = chart.current;
    if (!c) return;
    const elements = i === null ? [] : c.data.datasets.map((_, datasetIndex) => ({ datasetIndex, index: i }))
      .filter(e => c.isDatasetVisible(e.datasetIndex) && c.data.datasets[e.datasetIndex].data[e.index] !== null);
    c.setActiveElements(elements);
    const point = elements.length ? c.getDatasetMeta(elements[0].datasetIndex).data[i!] : null;
    c.tooltip?.setActiveElements(elements, point ? { x: point.x, y: point.y } : { x: 0, y: 0 });
    c.update('none');
  }
  const selected = index === null ? undefined : data[index];
  const reading = chartHeadline(block, data, selected?.key ?? null, projection, range).value;
  const readingLabel = selected?.label ?? (block.binding ? categoryRange(projection, range)?.label : data.at(-1)?.label) ?? 'No readings';
  return <>
    <div className="bar-plot" style={{ height: block.plot_height ?? (compact ? 176 : 242) }}>
      <canvas ref={ref} role="slider" tabIndex={data.length ? 0 : -1} aria-label={`${block.title ?? 'Chart'}, explore observations`}
        aria-valuemin={1} aria-valuemax={Math.max(1, data.length)} aria-valuenow={Math.max(1, (index ?? data.length - 1) + 1)}
        aria-valuetext={`${readingLabel}: ${reading === undefined ? 'No reading' : formatReading(block, reading)}`}
        onMouseLeave={() => inspect(null)} onBlur={() => inspect(null)} onKeyDown={event => {
          if (!data.length || !['ArrowLeft', 'ArrowRight', 'Home', 'End', 'Escape'].includes(event.key)) return;
          event.preventDefault();
          inspect(event.key === 'Escape' ? null : event.key === 'Home' ? 0 : event.key === 'End' ? data.length - 1 : Math.max(0, Math.min(data.length - 1, (index ?? data.length - 1) + (event.key === 'ArrowLeft' ? -1 : 1))));
        }} />
    </div>
  </>;
}
