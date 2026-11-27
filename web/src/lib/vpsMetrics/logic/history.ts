import type { HistoryPoint, HistoryRange, InstrumentId, TailnetHistory, Trend, TrendPoint, WindowId } from '../contracts';
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
/** The entity that names a peer's history. It matches the server's `tailnet_entity`. */
export const tailnetEntity = (identity: string): string => `tailnet:${identity}`;
export function buildTailnet(series: Record<string, TrendPoint[]>, identities: string[], start: number, end: number, gapSeconds: number): TailnetHistory {
  return {
    peers: Object.fromEntries(identities.map(identity => [identity, { direct: series[`${tailnetEntity(identity)}|tailnet.direct`] ?? [], changes: series[`${tailnetEntity(identity)}|tailnet.path_changes`] ?? [] }])),
    rates: { primary: series['tailnet.relay_packets_per_second'] ?? [], secondary: series['tailnet.path_discovery_per_second'] ?? [], start, end, gapSeconds }
  };
}
/** Bars for a path strip. A raw point has no width, so it lasts until the next point when that follows within `gapSeconds`. A hole stays a hole. */
export function pathBars(direct: TrendPoint[], start: number, end: number, gapSeconds: number): { x: number; width: number; share: number }[] {
  return direct.flatMap((point, index) => {
    const next = direct[index + 1];
    const to = Math.min(end, Math.max(point.end, next && next.timestamp - point.timestamp <= gapSeconds ? next.timestamp : point.timestamp)), from = Math.max(start, point.timestamp);
    return point.value === null || to <= from ? [] : [{ x: (from - start) / (end - start), width: (to - from) / (end - start), share: point.value }];
  });
}
