import type { HistoryPoint, MetricsApi, MetricsController, ProcessPage, TrendPoint, VpsMetricsState, WindowId } from './contracts';
import { createMetricsApi } from './api';
import { historySignals, metricsConfig } from './config';
import { buildTrends, historyRanges, joinHistory } from './logic/history';
export function createMetricsController(api: MetricsApi = createMetricsApi()): MetricsController {
  let state: VpsMetricsState = { latest: null, processes: null, window: '5m', history: {}, loading: true, historyLoading: false, failed: false, now: Date.now() / 1000, processError: null, historyError: null };
  const listeners = new Set<(state: VpsMetricsState) => void>();
  let visible = false, destroyed = false, generation = 0, latestFailed = false;
  let cycle: AbortController | null = null, history: AbortController | null = null;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let clock: ReturnType<typeof setInterval> | null = null;
  let historyBucket: number | null = null, historySequence: number | null = null;
  const emit = (patch: Partial<VpsMetricsState>) => { state = { ...state, ...patch }; for (const listener of listeners) listener(state); };
  const cancel = () => { generation++; cycle?.abort(); history?.abort(); cycle = null; history = null; if (timer !== null) clearTimeout(timer); timer = null; if (clock !== null) clearInterval(clock); clock = null; };
  async function readHistory(sequence: number | null): Promise<void> {
    const config = metricsConfig.windows[state.window];
    const end = Date.now() / 1000;
    const bucket = config.refreshSeconds ? Math.floor(end / config.refreshSeconds) : sequence;
    if (history || bucket === historyBucket && sequence === historySequence && config.refreshSeconds === 0 || config.refreshSeconds > 0 && bucket === historyBucket) return;
    const request = new AbortController(); history = request;
    const requestedWindow = state.window, token = generation, ranges = historyRanges(requestedWindow, end);
    emit({ historyLoading: true });
    try {
      const series: Record<string, TrendPoint[]> = {};
      // The adapter bounds the parallel signal reads to two server workers.
      await Promise.all(historySignals.map(async key => {
        const chunks: HistoryPoint[][] = [];
        for (const range of ranges) { const response = await api.history(key, range, request.signal); chunks.push(response.points.filter(point => point.timestamp >= range.start && point.timestamp < range.end)); }
        series[key] = joinHistory(chunks);
      }));
      if (request.signal.aborted || token !== generation || requestedWindow !== state.window) return;
      historyBucket = bucket; historySequence = sequence;
      emit({ history: buildTrends(series, ranges[0].start, end, requestedWindow === '5m' ? metricsConfig.rawGapSeconds : config.refreshSeconds * 1.5), historyError: null, failed: latestFailed || state.processError !== null });
    } catch (error) {
      if (!request.signal.aborted && token === generation) { request.abort(); emit({ failed: true, historyError: error instanceof Error ? error.message : 'History unavailable' }); }
    } finally {
      if (history === request) { history = null; emit({ historyLoading: false }); }
    }
  }
  async function readProcesses(signal: AbortSignal, sequence: number | null = null): Promise<ProcessPage> {
    // First page discovers a snapshot, later pages pin its immutable sequence.
    let first = await api.processes(sequence, 0, signal);
    const records = [...first.processes];
    while (first.next_offset !== null) {
      const page = await api.processes(first.sequence, first.next_offset, signal);
      if (page.sequence !== first.sequence || page.offset !== first.next_offset || page.next_offset !== null && page.next_offset <= page.offset) throw new Error('Invalid process snapshot page');
      records.push(...page.processes); first = { ...first, next_offset: page.next_offset };
    }
    if (first.availability !== 'unavailable' && records.length !== first.total) throw new Error('Incomplete process snapshot');
    return { ...first, processes: records, offset: 0, next_offset: null };
  }
  async function tick(): Promise<void> {
    if (!visible || destroyed || cycle) return;
    const request = new AbortController(); cycle = request; const token = generation;
    try {
      const [latest] = await Promise.all([api.latest(request.signal), api.activity(request.signal)]);
      if (request.signal.aborted || token !== generation) return;
      latestFailed = false;
      emit({ latest, now: Date.now() / 1000, failed: state.processError !== null || state.historyError !== null, loading: false });
      void readHistory(latest.sequence);
      if (latest.sequence !== null && state.processes?.sequence !== latest.sequence) {
        try {
          let processes: ProcessPage;
          try { processes = await readProcesses(request.signal); }
          catch (error) {
            if ((error as { status?: number }).status !== 410 || request.signal.aborted) throw error;
            // Expired partial data is discarded. Fresh latest evidence precedes the restart.
            const current = await api.latest(request.signal);
            if (!request.signal.aborted && token === generation) emit({ latest: current });
            processes = await readProcesses(request.signal, current.sequence);
          }
          if (!request.signal.aborted && token === generation) emit({ processes, processError: null, failed: latestFailed || state.historyError !== null });
        } catch (error) { if (!request.signal.aborted && token === generation) emit({ failed: true, processError: error instanceof Error ? error.message : 'Processes unavailable' }); }
      }
    } catch { if (!request.signal.aborted && token === generation) { latestFailed = true; emit({ failed: true, loading: false, now: Date.now() / 1000 }); } }
    finally {
      // A rejected latest/activity read can leave its Promise.all sibling pending.
      request.abort();
      if (cycle === request) cycle = null;
      if (visible && !destroyed && token === generation) timer = setTimeout(() => { timer = null; void tick(); }, metricsConfig.pollMilliseconds);
    }
  }
  return {
    subscribe(listener) { listeners.add(listener); listener(state); return () => listeners.delete(listener); },
    setVisible(value) { if (destroyed || visible === value) return; visible = value; cancel(); if (visible) { clock = setInterval(() => emit({ now: Date.now() / 1000 }), metricsConfig.clockMilliseconds); historyBucket = null; historySequence = null; void tick(); } },
    setWindow(window: WindowId) { if (destroyed || state.window === window) return; cancel(); historyBucket = null; historySequence = null; emit({ window, history: {}, historyLoading: visible }); if (visible) { clock = setInterval(() => emit({ now: Date.now() / 1000 }), metricsConfig.clockMilliseconds); void tick(); } },
    destroy() { destroyed = true; visible = false; cancel(); listeners.clear(); }
  };
}
