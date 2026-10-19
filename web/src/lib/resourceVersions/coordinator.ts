import type { QueryClient, Query } from '@tanstack/svelte-query';
import { fetchJson } from '../api';
import type { ResourceVersionsResponse } from './contracts';
import type { ResourceQueryData } from './query';
import { RESOURCE_VERSION_BATCH_SIZE } from './config';

/** One active reconciliation and one pending round, regardless of commit volume. */
export function createResourceCoordinator(client: QueryClient) {
  let generation = 0;
  let pending = false;
  let running: Promise<void> | null = null;
  let controller: AbortController | null = null;

  async function reconcile(expectedGeneration: number): Promise<void> {
    const observed = client.getQueryCache().getAll().filter(query => query.isActive());
    const keys = [...new Set(observed.flatMap(query =>
      typeof query.meta?.resourceKey === 'string' ? [query.meta.resourceKey] : []))];
    const versions = new Map<string, { epoch: string; revision: string | null }>();
    let failed = false;
    const abort = new AbortController();
    controller = abort;
    try {
      for (let offset = 0; offset < keys.length; offset += RESOURCE_VERSION_BATCH_SIZE) {
        const batch = keys.slice(offset, offset + RESOURCE_VERSION_BATCH_SIZE);
        const result = await fetchJson<ResourceVersionsResponse>(
          `/api/resource-versions?keys=${encodeURIComponent(JSON.stringify(batch))}`, { signal: abort.signal });
        if (expectedGeneration !== generation) return;
        if (typeof result.epoch !== 'string' || !Array.isArray(result.resources)) throw new Error('Invalid resource versions');
        for (const resource of result.resources) {
          if (batch.includes(resource.key) && (typeof resource.revision === 'string' || resource.revision === null)) {
            versions.set(resource.key, { epoch: result.epoch, revision: resource.revision });
          }
        }
      }
    } catch {
      failed = true;
    } finally {
      if (controller === abort) controller = null;
    }
    if (expectedGeneration !== generation) return;
    const changed = new Set<Query>();
    for (const query of observed) {
      // An unmounted query need not fetch. Its default staleTime forces a fresh read on return.
      if (!query.isActive()) continue;
      const key = query.meta?.resourceKey;
      const version = typeof key === 'string' ? versions.get(key) : undefined;
      const data = query.state.data as ResourceQueryData | undefined;
      if (failed || !version || version.revision === null || !data ||
          data.epoch !== version.epoch || data.revision !== version.revision) changed.add(query);
    }
    if (changed.size) {
      // An initial V1 read can still be in flight after the probe observes V2.
      // Explicit cancellation makes that first read lose authority too.
      await client.cancelQueries({ predicate: query => changed.has(query) });
      if (expectedGeneration !== generation) return;
      await client.invalidateQueries({ predicate: query => changed.has(query) });
    }
  }

  return {
    refresh(): Promise<void> {
      pending = true;
      if (running) return running;
      const expectedGeneration = generation;
      const task = (async () => {
        // Yield once so running is assigned even when there are no versioned reads.
        await Promise.resolve();
        while (pending && expectedGeneration === generation) {
          pending = false;
          await reconcile(expectedGeneration);
        }
      })();
      running = task;
      void task.finally(() => { if (running === task) running = null; });
      return task;
    },
    stop(): void {
      generation += 1;
      pending = false;
      controller?.abort();
      controller = null;
      running = null;
    }
  };
}
