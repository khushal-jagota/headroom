import { fetchJson } from '../api';
import type { HistoryResponse, LatestResponse, MetricsApi, ProcessPage } from './contracts';
import { metricsConfig } from './config';
/** One queue covers every read, including old generations that are still aborting. */
export function createMetricsApi(): MetricsApi {
  let active = 0;
  const waiting: (() => void)[] = [];
  async function bounded<T>(signal: AbortSignal, read: () => Promise<T>): Promise<T> {
    if (signal.aborted) throw new DOMException('Aborted', 'AbortError');
    if (active >= metricsConfig.readConcurrency) await new Promise<void>((resolve, reject) => {
      const ready = () => { signal.removeEventListener('abort', abort); resolve(); };
      const abort = () => { const index = waiting.indexOf(ready); if (index >= 0) waiting.splice(index, 1); reject(new DOMException('Aborted', 'AbortError')); };
      waiting.push(ready); signal.addEventListener('abort', abort, { once: true });
    });
    // A released slot belongs to this waiter until it starts or observes cancellation.
    else active++;
    try { if (signal.aborted) throw new DOMException('Aborted', 'AbortError'); return await read(); }
    finally { const next = waiting.shift(); if (next) next(); else active--; }
  }
  return {
    latest: signal => bounded(signal, () => fetchJson<LatestResponse>('/api/vps-metrics/latest', { signal })),
    activity: signal => fetchJson('/api/vps-metrics/activity', { method: 'POST', signal }),
    processes: (sequence, offset, signal) => bounded(signal, () => {
      const query = new URLSearchParams({ offset: String(offset), limit: String(metricsConfig.processPageRows) });
      if (sequence !== null) query.set('sequence', String(sequence));
      return fetchJson<ProcessPage>(`/api/vps-metrics/processes?${query}`, { signal });
    }),
    history: (key, range, signal) => bounded(signal, () => fetchJson<HistoryResponse>(`/api/vps-metrics/history?${new URLSearchParams({ signal: key, start: String(range.start), end: String(range.end), resolution: range.resolution })}`, { signal }))
  };
}
