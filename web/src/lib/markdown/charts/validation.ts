import { z } from 'zod';
import type { ChartBlock } from '../../goals/contracts';
import type { ChatChartSpec } from './contracts';

// Bound authored readings so chart spans and stacked totals cannot overflow.
const reading = z.number().finite().min(-Number.MAX_SAFE_INTEGER).max(Number.MAX_SAFE_INTEGER);
const point = z.strictObject({ label: z.string(), value: reading.nullable() });
const series = z.strictObject({ name: z.string(), points: z.array(point).min(1) })
  .refine(value => new Set(value.points.map(item => item.label)).size === value.points.length);
const schema = z.strictObject({
  kind: z.enum(['bar', 'line']),
  title: z.string().optional(),
  unit: z.string().optional(),
  format: z.strictObject({
    prefix: z.string().optional(), suffix: z.string().optional(),
    decimals: z.number().int().min(0).max(20).optional()
  }).optional(),
  stacked: z.boolean().optional(),
  series: z.array(series).min(1)
}).refine(value => value.kind === 'bar' || value.stacked === undefined);

/** Invalid authored data stays ordinary code, including unsupported nested options. */
export function parseChatChart(source: string): ChartBlock | null {
  let raw: unknown;
  try { raw = JSON.parse(source); } catch { return null; }
  const parsed = schema.safeParse(raw);
  if (!parsed.success) return null;
  const spec: ChatChartSpec = parsed.data;
  return {
    ...spec, type: 'chart', id: 'chat-chart', placement: 'primary',
    series: spec.series.map((item, index) => ({
      ...item, key: index === 0 ? 'actual' : `inline-${index}`
    }))
  };
}
