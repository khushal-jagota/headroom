import type { QueryClient, Query } from '@tanstack/svelte-query';
import { INACTIVE_QUERY_MAX_BYTES, INACTIVE_QUERY_MAX_ENTRIES } from './config';

/** Only decoded server responses enter this budget. Drafts and outgoing messages do not. */
export function boundInactiveQueries(client: QueryClient, limits = {
  entries: INACTIVE_QUERY_MAX_ENTRIES, bytes: INACTIVE_QUERY_MAX_BYTES
}): () => void {
  const inactive = new Map<Query, number>();
  let bytes = 0;
  let pruning = false;
  function forget(query: Query): void {
    const size = inactive.get(query);
    if (size !== undefined) { bytes -= size; inactive.delete(query); }
  }
  return client.getQueryCache().subscribe(event => {
    const query = event.query;
    if (event.type === 'removed') { forget(query); return; }
    // Unknown imperative caches do not acquire an assumed size or eviction policy.
    if (query.meta?.resourceBody !== true) return;
    forget(query);
    if (query.getObserversCount() > 0 || query.state.data === undefined) return;
    const size = (query.state.data as { decodedBytes?: number }).decodedBytes;
    if (typeof size !== 'number') return;
    if (size > limits.bytes) { client.getQueryCache().remove(query); return; }
    inactive.set(query, size);
    bytes += size;
    if (pruning) return;
    pruning = true;
    try {
      while (inactive.size > limits.entries || bytes > limits.bytes) {
        const oldest = inactive.keys().next().value as Query | undefined;
        if (!oldest) break;
        forget(oldest);
        client.getQueryCache().remove(oldest);
      }
    } finally { pruning = false; }
  });
}
