import type { QueryClient, Query } from '@tanstack/svelte-query';
import { fetchJson } from '../api';
import type { ResourceVersionsResponse } from './contracts';
import type { ResourceQueryData } from './query';
import { RESOURCE_VERSION_BATCH_SIZE } from './config';

type Version = { epoch: string; revision: string | null };
/**
 * One body read that a round started and that did not settle yet.
 *
 * `wanted` is set when a later round finds this query changed while the read is in
 * flight: the version that round saw, or `true` when there is no version to compare.
 * `settled` resolves with the one read that followed this one, or null.
 */
type Reading = { wanted: Version | true | null; settled: Promise<Reading | null> };

function current(query: Query, version: Version | undefined): boolean {
  const data = query.state.data as ResourceQueryData | undefined;
  return !!version && version.revision !== null && !!data &&
    data.epoch === version.epoch && data.revision === version.revision;
}

/**
 * One active revision probe and one pending round, regardless of commit volume.
 *
 * A round checks revisions and starts a read for each changed body. It does not wait for
 * those reads, so one slow body cannot hold the next round or any other body. A later
 * round leaves a read in flight alone, because a restart on each round can keep a slow
 * read from ever finishing. That read is followed by at most one more read of the same
 * body, and only a round sets that up, so the reads cannot feed themselves.
 */
export function createResourceCoordinator(client: QueryClient) {
  let generation = 0;
  let pending = false;
  let probing: Promise<void> | null = null;
  let controller: AbortController | null = null;
  /** Resolves when the bodies that the last round found changed are on screen. */
  let shown: Promise<void> = Promise.resolve();
  const reading = new Map<Query, Reading>();

  function read(query: Query, expectedGeneration: number): Reading {
    const entry: Reading = { wanted: null, settled: Promise.resolve(null) };
    entry.settled = client.invalidateQueries({ predicate: candidate => candidate === query })
      .catch(() => undefined)
      .then(() => {
        if (reading.get(query) !== entry) return null;
        reading.delete(query);
        if (expectedGeneration !== generation || entry.wanted === null || !query.isActive()) return null;
        // The read can already hold what the later round saw. If not, read once more.
        if (entry.wanted !== true && current(query, entry.wanted)) return null;
        return read(query, expectedGeneration);
      });
    reading.set(query, entry);
    return entry;
  }

  async function reconcile(expectedGeneration: number): Promise<void> {
    const observed = client.getQueryCache().getAll().filter(query =>
      query.isActive() && query.meta?.followsChangeStream !== false);
    const keys = [...new Set(observed.flatMap(query =>
      typeof query.meta?.resourceKey === 'string' ? [query.meta.resourceKey] : []))];
    const versions = new Map<string, Version>();
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
    const waits: Promise<unknown>[] = [];
    for (const query of observed) {
      // An unmounted query need not fetch. Its default staleTime forces a fresh read on return.
      if (!query.isActive()) continue;
      const key = query.meta?.resourceKey;
      const version = failed || typeof key !== 'string' ? undefined : versions.get(key);
      if (current(query, version)) continue;
      const started = reading.get(query);
      if (!started) { changed.add(query); continue; }
      // That read can be older than this probe. The one read after it is not.
      started.wanted = version && version.revision !== null ? version : true;
      waits.push(started.settled.then(next => next?.settled));
    }
    if (changed.size) {
      // An initial V1 read can still be in flight after the probe observes V2.
      // Explicit cancellation makes that first read lose authority too.
      await client.cancelQueries({ predicate: query => changed.has(query) });
      if (expectedGeneration !== generation) return;
      for (const query of changed) waits.push(read(query, expectedGeneration).settled);
    }
    shown = Promise.all(waits).then(() => undefined);
  }

  return {
    /** Resolves when the revision check ended and the bodies that it found changed are on screen. */
    refresh(): Promise<void> {
      pending = true;
      if (!probing) {
        const expectedGeneration = generation;
        const task = (async () => {
          // Yield once so probing is assigned even when there are no versioned reads.
          await Promise.resolve();
          while (pending && expectedGeneration === generation) {
            pending = false;
            await reconcile(expectedGeneration);
          }
        })();
        probing = task;
        void task.finally(() => { if (probing === task) probing = null; });
      }
      return probing.then(() => shown);
    },
    stop(): void {
      generation += 1;
      pending = false;
      controller?.abort();
      controller = null;
      probing = null;
      reading.clear();
      shown = Promise.resolve();
    }
  };
}
