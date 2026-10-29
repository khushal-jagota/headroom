/** Adapted from the approved sheet's components/goal-indicators.tsx.
 * Example menus and sample state are replaced by the canonical chart contract.
 */
import React, { useState } from 'react';
import { motion, useReducedMotion } from 'motion/react';
import type { ChartBlock, ChartSeries, CategoryChartRange, CategoryChartSummary } from '../../goals/contracts';
import type { LineChartDatum } from './line-chart-integrated';
import { AnimatedCounter } from './animated-counter';
import { motionTokens } from '../lib/motion-tokens';
import { formatReading, allocationData, seriesKey } from '../presentation';

export const seriesColor = (series: ChartSeries, index: number, palette = 'allocation', active = false) =>
  `var(--chart-${palette}-${series.color ?? (index === 0 ? 'blue' : index === 1 ? 'blue-muted' : 'blue-dark')}${active ? '-active' : ''})`;

function useTransition() {
  return useReducedMotion() ? { duration: 0 } : motionTokens.spring.smooth;
}

export function ProgressCard({ block, value, summary }: { block: ChartBlock; value: number | undefined; summary?: CategoryChartSummary }) {
  const target = block.target, transition = useTransition();
  const validTarget = target !== undefined && Number.isFinite(target) && target > 0;
  const good = block.binding ? summary?.meets_expectation === true : value !== undefined && validTarget && value >= target;
  const partial = block.binding && summary?.meets_expectation == null;
  const tone = partial ? 'var(--chart-muted)' : good ? 'var(--accent-done)' : 'var(--accent-bright)';
  return <div className="meter-plot">
    <div className="meter-track" role="progressbar" aria-label={block.title ?? 'Progress'}
      aria-valuemin={0} aria-valuemax={validTarget ? target : undefined} aria-valuenow={value === undefined || !validTarget ? undefined : Math.max(0, Math.min(value, target))}
      aria-valuetext={value === undefined ? 'No reading' : !validTarget ? `${formatReading(block, value)}. Target reading unavailable.` : `${formatReading(block, value)} of ${formatReading(block, target)}`}>
      <motion.div className="meter-fill" initial={false} animate={{ width: `${value === undefined || !validTarget ? 0 : Math.max(0, Math.min(value / target, 1)) * 100}%`, backgroundColor: tone }} transition={transition} />
    </div>
    <div className="meter-scale"><span>0</span><span>{block.scale_label ?? (validTarget ? formatReading(block, target) : '—')}</span></div>
  </div>;
}

export function TimeBudgetCard({ block, value, summary }: { block: ChartBlock; value: number | undefined; summary?: CategoryChartSummary }) {
  const [active, setActive] = useState<number | null>(null);
  const budget = block.target;
  const validBudget = budget !== undefined && Number.isFinite(budget) && budget > 0;
  const remaining = block.binding ? summary?.difference == null ? undefined : -summary.difference : value === undefined || !validBudget ? undefined : budget - value;
  const over = block.binding ? summary?.meets_expectation === false : remaining !== undefined && remaining < 0;
  const partial = block.binding && summary?.meets_expectation == null;
  const tone = partial ? 'var(--chart-muted)' : over ? 'var(--accent-error)' : 'var(--accent-bright)';
  const transition = useTransition(), circumference = 2 * Math.PI * 80;
  const values = value === undefined || value < 0 || !validBudget || remaining === undefined ? [] : [Math.min(value, budget), Math.max(0, remaining)];
  const names = ['Spent', block.binding && summary?.meets_expectation == null ? 'Difference' : remaining !== undefined && remaining < 0 ? 'Over budget' : 'Remaining'];
  let offset = 0;
  const display = active === 1 ? (remaining === undefined ? undefined : Math.abs(remaining)) : value;
  return <div className="ring-layout allocation-layout">
    <div className="ring-chart" role="img" aria-label={value === undefined ? 'No reading' : !validBudget || remaining === undefined ? `${formatReading(block, value)} spent. Planned reading unavailable.` : `${formatReading(block, value)} spent of ${formatReading(block, budget)} planned. ${formatReading(block, Math.abs(remaining))} ${names[1].toLowerCase()}.`}>
      <svg viewBox="0 0 200 200" aria-hidden="true" onPointerLeave={() => setActive(null)}>
        {values.map((amount, i) => {
          const start = offset; offset += amount / budget!;
          return <g key={i}>
            <motion.circle cx="100" cy="100" r="80" fill="none" transform="rotate(-90 100 100)" initial={false}
              animate={{ strokeDasharray: `${Math.max(0, amount / budget! * circumference - (amount === budget ? 0 : 5))} ${circumference}`, strokeDashoffset: -start * circumference, stroke: i === 0 ? tone : 'var(--chart-budget-track)', strokeWidth: active === i ? 16 : 12, opacity: active === null || active === i ? 1 : .5 }}
              transition={transition} style={{ pointerEvents: 'none' }} />
            {amount > 0 && <circle data-segment={i} cx="100" cy="100" r="80" fill="none" stroke="transparent" strokeWidth="28" transform="rotate(-90 100 100)"
              strokeDasharray={`${amount / budget! * circumference} ${circumference}`} strokeDashoffset={-start * circumference} style={{ pointerEvents: 'stroke' }} onPointerEnter={() => setActive(i)} onClick={() => setActive(i)} />}
          </g>;
        })}
      </svg>
      <div className="ring-center">{display === undefined ? <span className="missing-value">—</span> : <AnimatedCounter value={display} prefix={block.format?.prefix} suffix={block.format?.suffix ?? block.unit} decimals={block.format?.decimals ?? 0} />}<span>{active === 1 ? names[1] : 'Spent'}</span></div>
    </div>
    <div className="allocation-legend" data-active={active ?? undefined}>{names.map((name, i) => {
      const amount = i === 0 ? value : remaining === undefined ? undefined : Math.abs(remaining);
      return <motion.button key={i} type="button" data-selected={active === i}
        onFocus={() => setActive(i)} onBlur={() => setActive(null)} onMouseEnter={() => setActive(i)} onMouseLeave={() => setActive(null)} onClick={() => setActive(i)}
        animate={{ opacity: active === null || active === i ? 1 : .5 }} transition={transition}
        aria-label={`${name}, ${amount === undefined ? 'No reading' : formatReading(block, amount)}`}>
        <i style={{ background: i === 0 ? tone : partial ? 'var(--chart-muted)' : over ? 'var(--accent-error)' : 'var(--chart-budget-remaining)' }} /><span>{name}</span><strong>{amount === undefined ? '—' : formatReading(block, amount)}</strong>
      </motion.button>;
    })}</div>
  </div>;
}

export function AllocationCard({ block, data, series, range, activeKey = null }: { block: ChartBlock; data: LineChartDatum[]; series: ChartSeries[]; range?: CategoryChartRange; activeKey?: string | null }) {
  const [active, setActive] = useState<string | null>(null), transition = useTransition();
  const geometry = allocationData(data, series, range, activeKey);
  const { reading, total } = geometry;
  const percentage = block.allocation_mode === 'percentage';
  const entries = geometry.series.map((item, index) => ({ item, key: seriesKey(item, index), color: seriesColor(item, index) }));
  if (active !== null && !entries.some(entry => entry.key === active)) setActive(null);
  const activeEntry = entries.find(entry => entry.key === active);
  const displayValue = (value: number | undefined) => value === undefined || (percentage && !total) ? undefined : percentage ? value / total! * 100 : value;
  const format = (value: number | undefined) => {
    const display = displayValue(value);
    return display === undefined ? 'No reading' : percentage ? `${display.toLocaleString('en-US', { maximumFractionDigits: 1 })}%` : formatReading(block, display);
  };
  const activeValue = activeEntry ? displayValue(reading?.values[activeEntry.key]) : percentage ? (total ? 100 : undefined) : total;
  const circumference = 2 * Math.PI * 80;
  let offset = 0;
  return <div className="ring-layout allocation-layout">
    <div className="ring-chart" role="img" aria-label={entries.map(({ item, key }) => `${item.name}: ${format(reading?.values[key])}`).join(', ')}>
      <svg viewBox="0 0 200 200" aria-hidden="true" onPointerLeave={() => setActive(null)}>{entries.map(({ item, key, color }, index) => {
        const amount = reading?.values[key], fraction = total && amount !== undefined ? amount / total : 0;
        const start = offset; offset += fraction;
        return <g key={key}>
          <motion.circle cx="100" cy="100" r="80" fill="none" transform="rotate(-90 100 100)" initial={false}
            animate={{ strokeDasharray: `${Math.max(0, fraction * circumference - (fraction === 1 ? 0 : 5))} ${circumference}`, strokeDashoffset: -start * circumference, strokeWidth: active === key ? 16 : 12, stroke: active === key ? seriesColor(item, index, 'allocation', true) : color, opacity: active === null || active === key ? 1 : .5 }} transition={transition} style={{ pointerEvents: 'none' }} />
          {fraction > 0 && <circle data-segment={key} cx="100" cy="100" r="80" fill="none" stroke="transparent" strokeWidth="28" transform="rotate(-90 100 100)"
            strokeDasharray={`${fraction * circumference} ${circumference}`} strokeDashoffset={-start * circumference} style={{ pointerEvents: 'stroke' }} onPointerEnter={() => setActive(key)} onClick={() => setActive(key)} />}
        </g>;
      })}</svg>
      <div className="ring-center">{activeValue === undefined ? <span className="missing-value">—</span> : <AnimatedCounter value={activeValue} prefix={percentage ? undefined : block.format?.prefix} suffix={percentage ? '%' : block.format?.suffix ?? block.unit} decimals={percentage ? (activeEntry ? 1 : 0) : block.format?.decimals ?? 0} />}<span>{activeEntry?.item.name ?? (percentage ? 'Total' : block.total_label ?? 'Total')}</span></div>
    </div>
    <div className="allocation-legend" data-active={active ?? undefined}>{entries.map(({ item, key, color }) => <motion.button key={key} type="button" data-selected={active === key}
      onFocus={() => setActive(key)} onBlur={() => setActive(null)} onMouseEnter={() => setActive(key)} onMouseLeave={() => setActive(null)} onClick={() => setActive(current => current === key ? null : key)}
      animate={{ opacity: active === null || active === key ? 1 : .5 }} transition={transition}
      aria-label={`${item.name}, ${format(reading?.values[key])}${!percentage && total && reading?.values[key] !== undefined ? `, ${Math.round(reading.values[key]! / total * 100)} percent` : ''}`}>
      <i style={{ background: color }} /><span>{item.name}</span><strong>{format(reading?.values[key])}</strong>
    </motion.button>)}</div>
  </div>;
}
