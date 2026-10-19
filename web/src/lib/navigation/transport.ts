import type { Fence, SnapshotResponse } from './contracts';
import { parseSnapshotResponse } from './validation';
import { PlannerFetchError } from '../api';

/** An ordinary failed read, distinct from internal protocol and recovery state. */
export class NavigationRequestError extends PlannerFetchError {}

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
      let response: Response;
      try { response = await request(`/api/navigation/snapshot${query}`, { signal, cache: 'no-store' }); }
      catch (error) {
        if (signal.aborted) throw error;
        throw new NavigationRequestError('network error','network');
      }
      if (!response.ok) {
        let parsed: unknown;
        try { parsed = await response.json(); } catch { parsed = null; }
        if (parsed && typeof parsed === 'object' && 'error' in parsed && parsed.error
          && typeof parsed.error === 'object' && 'code' in parsed.error) {
          const envelope = parsed.error as {code: unknown; message?: unknown; detail?: unknown};
          throw new NavigationRequestError(String(envelope.message || 'request failed'),String(envelope.code),{
            status:response.status,detail:envelope.detail,isPlannerError:true,
          });
        }
        throw new NavigationRequestError(`HTTP ${response.status}`,'http_error',{status:response.status});
      }
      let parsed: unknown;
      try { parsed = await response.json(); }
      catch { throw new NavigationRequestError('invalid JSON in response','bad_json',{status:response.status}); }
      return parseSnapshotResponse(parsed);
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
