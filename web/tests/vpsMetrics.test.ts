import { describe, expect, it } from 'vitest';
import type { HistoryPoint, ProcessRecord, Reading, Sample } from '../src/lib/vpsMetrics/contracts';
import { cpuShare, instruments, processRows, tailnetRows } from '../src/lib/vpsMetrics/presentation';
import { historyRanges, joinHistory, pathBars, subtractHistory } from '../src/lib/vpsMetrics/logic/history';
const reading = (value: number | null): Reading => ({ value, unit: '%', reason: value === null ? 'missing' : null });
const record = (name: string, cpu: number | null, memory: number | null): ProcessRecord => ({ identity: { boot_id: 'boot', pid: 1, start_ticks: 1 }, parent_pid: 0, uid: 0, name, state: 'S', service: '', metrics: { cpu_percent: reading(cpu), rss_bytes: { ...reading(memory), unit: 'bytes' } } });
const point = (timestamp: number, value: number | null, boot_id = 'boot'): HistoryPoint => ({ timestamp, end: timestamp + 60, value, boot_id, unit: 'bytes', reason: null, statistics: null });
describe('retained VPS evidence presentation', () => {
  it('normalizes per-core CPU without inventing a missing denominator', () => {
    expect(cpuShare(reading(400), 12).value).toBeCloseTo(33.3333);
    expect(cpuShare(reading(400), 0).value).toBeNull();
    expect(cpuShare(reading(null), 12).value).toBeNull();
  });
  it('keeps partial sums independent and process bars relative to the maximum after stable ordering', () => {
    const rows = processRows([record('old', 12, null), record('new', 48, 100), record('new', null, 50), record('missing', null, null)], 12, ['old']);
    expect(rows.map(row => row.name)).toEqual(['old', 'new', 'missing']);
    expect(rows[0].share).toBe(0.25);
    expect(rows[1]).toMatchObject({ cpu: { value: 4, reason: '1 unavailable' }, memory: { value: 150, reason: null }, partial: true, share: 1 });
    expect(rows[2].cpu.value).toBeNull();
    expect(rows[2].memory.value).toBeNull();
  });

  it('uses pressure, free bytes and core capacity thresholds and never scores network or disk', () => {
    const sample: Sample = { timestamp: 0, monotonic: 0, boot_id: 'boot', expected_interval: 2, core_count: 12, services: [], processes: [], process_count: 0, process_availability: 'available', process_reason: null, disk_total_members: [], network_total_members: [], metrics: {
      cpu_percent: reading(81), 'pressure.cpu.some.avg10': reading(40), memory_total_bytes: reading(32e9), memory_available_bytes: reading(2e9), load_1: reading(18), 'pressure.io.some.avg10': reading(15), 'filesystem.total_bytes': reading(100), 'filesystem.available_bytes': reading(1), 'network.receive_bytes_per_second': reading(1e12)
    } };
    const raised = instruments(sample);
    expect(raised.map(card => [card.id, card.severity])).toEqual([['cpu', 'raised'], ['memory', 'raised'], ['load', 'raised'], ['io', 'raised'], ['network', 'normal'], ['disk', 'normal']]);
    expect(raised.find(card => card.id === 'disk')?.primary.value).toBe(99);
    sample.metrics['pressure.cpu.some.avg10'].value = 41;
    sample.metrics.memory_available_bytes.value = 2e9 - 1;
    sample.metrics.load_1.value = 18.1;
    sample.metrics['pressure.io.some.avg10'].value = 15.1;
    expect(instruments(sample).slice(0, 4).map(card => card.severity)).toEqual(['wrong', 'wrong', 'wrong', 'wrong']);
  });
  it('uses adjacent bounded thirty-day ranges and joins immutable timestamps once', () => {
    const ranges = historyRanges('30d', 3000000);
    expect(ranges).toEqual([{ start: 408000, end: 1704000, resolution: 'coarse' }, { start: 1704000, end: 3000000, resolution: 'coarse' }]);
    expect(joinHistory([[point(60, 1)], [point(60, 1), point(120, 2)]]).map(p => p.timestamp)).toEqual([60, 120]);
  });
  it('aligns arithmetic by timestamp and boot identity and preserves missing operands', () => {
    const result = subtractHistory(joinHistory([[point(0, 100), point(60, 100), point(120, 100), point(180, null)]]), joinHistory([[point(60, 40), point(120, 30, 'other'), point(180, 10)]]));
    expect(result.map(p => p.value)).toEqual([null, 60, null, null]);
  });
  it('names each tailnet path and leaves a hole where the history has none', () => {
    const peer = (identity: string, path: 'direct' | 'relayed' | 'idle', endpoint: string | null) => ({ identity, name: identity, path, endpoint, relay: 'par', last_handshake: 940, metrics: {} });
    const sample = { timestamp: 1000, tailnet: { availability: 'available', reason: null, home_relay: 'fra', peers: [peer('mac', 'direct', '86.0.250.169:48077'), peer('phone', 'relayed', null), peer('tv', 'idle', null)] } } as unknown as Sample;
    expect(tailnetRows(sample).map(row => [row.path, row.direct, row.handshake])).toEqual([['Direct · 86.0.250.169:48077', true, 'handshake 1 min ago'], ['Relayed via par', false, 'handshake 1 min ago'], ['Idle, no path in use', null, 'handshake 1 min ago']]);
    const bars = pathBars(joinHistory([[point(0, 1), point(60, null), point(120, 0), point(300, 1)]]), 0, 400, 90);
    expect(bars.map(bar => [bar.x * 400, bar.width * 400, bar.share])).toEqual([[0, 60, 1], [120, 60, 0], [300, 60, 1]]);
  });
});
