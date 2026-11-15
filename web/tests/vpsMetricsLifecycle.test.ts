import { afterEach, describe, expect, it, vi } from 'vitest';
import type { LatestResponse, MetricsApi, ProcessPage, RetentionCoverage, VpsMetricsState } from '../src/lib/vpsMetrics/contracts';
import { createMetricsApi } from '../src/lib/vpsMetrics/api';
import { createMetricsController } from '../src/lib/vpsMetrics/controller';
const coverage: RetentionCoverage = { requested_start: null, retained_start: null, retained_end: null, budget_evicted: false, policy_seconds: 3600 };
const latest = (sequence: number): LatestResponse => ({ sequence, sample: null, coverage, collection: { availability: 'available', reason: null, last_sample_at: 100, last_attempt_at: 100, cadence_seconds: 2 } });
const page = (sequence: number, next_offset: number | null): ProcessPage => ({ sequence, timestamp: 100, processes: [], total: 0, offset: 0, next_offset, availability: 'available', reason: null, coverage });
const flush = async () => { for (let i = 0; i < 40; i++) await Promise.resolve(); };
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });
describe('VPS visible read lifecycle', () => {
  it('bounds all adapter reads to two and removes canceled queued work', async () => {
    const finish: (() => void)[] = [];
    let active = 0, maximum = 0;
    vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>(resolve => {
      maximum = Math.max(maximum, ++active);
      finish.push(() => { active--; resolve(new Response('{}')); });
    })));
    const api = createMetricsApi(), abort = new AbortController(), queued = new AbortController();
    const reads = [api.latest(abort.signal), api.processes(null, 0, abort.signal), api.latest(queued.signal)];
    const settled = Promise.allSettled(reads);
    queued.abort(); await flush();
    expect(maximum).toBe(2);
    for (const end of finish) end();
    const result = await settled;
    expect(result.map(item => item.status)).toEqual(['fulfilled', 'fulfilled', 'rejected']);
    expect(fetch).toHaveBeenCalledTimes(2);
  });
  it('ages retained evidence while the next request hangs and cancels clocks and reads when hidden', async () => {
    vi.useFakeTimers(); vi.setSystemTime(100000);
    let count = 0, pendingSignal: AbortSignal | null = null;
    const api: MetricsApi = {
      latest: async signal => { if (++count === 1) return latest(1); pendingSignal = signal; return new Promise(() => {}); },
      activity: async () => ({ active_until: 130 }), processes: async () => page(1, null),
      history: async (signal, range) => ({ signal, entity: null, resolution: range.resolution, points: [], coverage })
    };
    const controller = createMetricsController(api);
    let state: VpsMetricsState | undefined;
    controller.subscribe(value => { state = value; });
    controller.setVisible(true); await flush();
    await vi.advanceTimersByTimeAsync(7000);
    expect(state?.now).toBe(107);
    expect(state?.latest?.collection.last_sample_at).toBe(100);
    controller.setVisible(false);
    expect((pendingSignal as AbortSignal | null)?.aborted).toBe(true);
    await vi.advanceTimersByTimeAsync(5000);
    expect(state?.now).toBe(107);
    expect(count).toBe(2);
    controller.destroy();
  });
  it('aborts a pending sibling when the other current read fails', async () => {
    vi.useFakeTimers();
    let pendingSignal: AbortSignal | null = null;
    const api: MetricsApi = {
      latest: signal => { pendingSignal = signal; return new Promise(() => {}); },
      activity: async () => { throw new Error('Lease unavailable'); },
      processes: async () => page(1, null),
      history: async (signal, range) => ({ signal, entity: null, resolution: range.resolution, points: [], coverage })
    };
    const controller = createMetricsController(api);
    controller.setVisible(true); await flush();
    expect((pendingSignal as AbortSignal | null)?.aborted).toBe(true);
    controller.setVisible(false);
    expect(vi.getTimerCount()).toBe(0);
    controller.destroy();
  });
});
