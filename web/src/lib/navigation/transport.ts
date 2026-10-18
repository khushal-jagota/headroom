import type { Fence, SnapshotResponse } from './contracts';
import { parseSnapshotResponse } from './validation';

export interface NavigationStreamHandlers {
  batch(data: unknown, cursor: string): void;
  reset(): void;
  error(): void;
}
export interface NavigationTransport {
  snapshot(signal: AbortSignal, fence?: Fence): Promise<SnapshotResponse>;
  connect(cursor: string, handlers: NavigationStreamHandlers, signal: AbortSignal): () => void;
}

/** Each connection is new: native EventSource retry must never resume a received-only id. */
export function createNavigationTransport(options: {
  fetch?: typeof fetch;
  eventSource?: (url: string) => EventSource;
} = {}): NavigationTransport {
  const request = options.fetch ?? globalThis.fetch.bind(globalThis);
  const source = options.eventSource ?? ((url: string) => new EventSource(url));
  return {
    async snapshot(signal, fence) {
      const query = fence ? `?fence=${encodeURIComponent(JSON.stringify(fence))}` : '';
      const response = await request(`/api/navigation/snapshot${query}`, { signal, cache: 'no-store' });
      if (!response.ok) throw new Error(`navigation snapshot HTTP ${response.status}`);
      return parseSnapshotResponse(await response.json());
    },
    connect(cursor, handlers, signal) {
      const stream = source(`/api/navigation/events?cursor=${encodeURIComponent(cursor)}`);
      let closed = false;
      const close = () => {
        if (closed) return;
        closed = true;
        stream.close();
        signal.removeEventListener('abort', close);
      };
      stream.addEventListener('batch', (event) => {
        if (closed) return;
        const message = event as MessageEvent<string>;
        try { handlers.batch(JSON.parse(message.data), message.lastEventId); }
        catch { close(); handlers.reset(); }
      });
      stream.addEventListener('reset', () => { if (!closed) { close(); handlers.reset(); } });
      stream.onerror = () => { if (!closed) { close(); handlers.error(); } };
      if (signal.aborted) close();
      else signal.addEventListener('abort', close, { once: true });
      return close;
    },
  };
}
