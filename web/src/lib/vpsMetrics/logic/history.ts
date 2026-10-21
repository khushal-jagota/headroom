import type { HistoryPoint, HistoryRange, InstrumentId, Trend, TrendPoint, WindowId } from '../contracts';
import { metricsConfig } from '../config';
export function historyRanges(window: WindowId, end: number): HistoryRange[] {
  const config = metricsConfig.windows[window];
  const start = end - config.seconds;
  const ranges: HistoryRange[] = [];
  for (let cursor = start; cursor < end; cursor += config.chunkSeconds) ranges.push({ start: cursor, end: Math.min(cursor + config.chunkSeconds, end), resolution: config.resolution });
  return ranges;
}
export function joinHistory(chunks: HistoryPoint[][]): TrendPoint[] {
  const points = new Map<number, TrendPoint>();
  for (const chunk of chunks) for (const point of chunk) points.set(point.timestamp, { timestamp: point.timestamp, end: point.end, value: point.value, boot_id: point.boot_id });
  return [...points.values()].sort((a, b) => a.timestamp - b.timestamp);
}
export function subtractHistory(totals: TrendPoint[], available: TrendPoint[]): TrendPoint[] {
  const operand = new Map(available.map(point => [point.timestamp, point]));
  return totals.map(point => {
    const other = operand.get(point.timestamp);
    return { ...point, value: point.value === null || !other || other.value === null || other.boot_id !== point.boot_id ? null : point.value - other.value };
  });
}
export function buildTrends(series: Record<string, TrendPoint[]>, start: number, end: number, gapSeconds: number): Record<InstrumentId, Trend> {
  const single = (key: string): Trend => ({ primary: series[key] ?? [], secondary: [], start, end, gapSeconds });
  const pair = (a: string, b: string): Trend => ({ ...single(a), secondary: series[b] ?? [] });
  return { cpu: single('cpu_percent'), memory: { ...single('memory_total_bytes'), primary: subtractHistory(series.memory_total_bytes ?? [], series.memory_available_bytes ?? []) }, load: single('load_1'), disk: { ...single('filesystem.total_bytes'), primary: subtractHistory(series['filesystem.total_bytes'] ?? [], series['filesystem.available_bytes'] ?? []).map((point, index) => ({ ...point, value: point.value === null || !series['filesystem.total_bytes'][index]?.value ? null : point.value / series['filesystem.total_bytes'][index].value! * 100 })) }, io: pair('disk.write_bytes_per_second', 'disk.read_bytes_per_second'), network: pair('network.receive_bytes_per_second', 'network.transmit_bytes_per_second') };
}
