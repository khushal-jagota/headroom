import { fetchJson, type FetchOptions } from "./api";
import { countInvalidation } from "./debug";

type Fetcher<T> = (signal: AbortSignal) => Promise<T>;

type ResourceState<T> = {
  data: T | undefined;
  error: unknown;
  loading: boolean;
  stale: boolean;
};

type Entry<T> = {
  key: string;
  fetcher: Fetcher<T>;
  state: ResourceState<T>;
  subscribers: number;
  version: number;
  promise: Promise<T> | null;
  controller: AbortController | null;
};

const entries = new Map<string, Entry<unknown>>();

function getEntry<T>(key: string, fetcher: Fetcher<T>): Entry<T> {
  let entry = entries.get(key) as Entry<T> | undefined;
  if (!entry) {
    const state = $state({
      data: undefined,
      error: null,
      loading: false,
      stale: true
    });
    entry = {
      key,
      fetcher,
      state,
      subscribers: 0,
      version: 0,
      promise: null,
      controller: null
    };
    entries.set(key, entry as Entry<unknown>);
  } else {
    entry.fetcher = fetcher;
  }
  return entry;
}

async function runFetch<T>(entry: Entry<T>, force = false): Promise<T> {
  if (entry.promise && !force) return entry.promise;
  entry.controller?.abort();
  const controller = new AbortController();
  const version = entry.version + 1;
  entry.version = version;
  entry.controller = controller;
  entry.state.loading = true;
  entry.state.error = null;
  const firstLoad = entry.state.data === undefined;
  if (firstLoad) entry.state.stale = false;

  const promise = entry.fetcher(controller.signal).then(
    (data) => {
      if (entry.version === version) {
        entry.state.data = data;
        entry.state.error = null;
        entry.state.stale = false;
        entry.state.loading = false;
        entry.promise = null;
        entry.controller = null;
      }
      return data;
    },
    (error) => {
      if (entry.version === version) {
        entry.state.error = error;
        entry.state.loading = false;
        entry.promise = null;
        entry.controller = null;
        entry.state.stale = entry.state.data !== undefined;
      }
      throw error;
    }
  );
  entry.promise = promise;
  return promise;
}

export class ResourceHandle<T> {
  #entry: Entry<T>;
  #disposed = false;

  constructor(entry: Entry<T>) {
    this.#entry = entry;
    this.#entry.subscribers += 1;
    if (this.#entry.state.stale || this.#entry.state.data === undefined) {
      void runFetch(this.#entry).catch(() => undefined);
    }
  }

  get data(): T | undefined {
    return this.#entry.state.data;
  }

  get error(): unknown {
    return this.#entry.state.error;
  }

  get loading(): boolean {
    return this.#entry.state.loading;
  }

  get stale(): boolean {
    return this.#entry.state.stale;
  }

  refresh(): Promise<T> {
    return runFetch(this.#entry, true);
  }

  invalidate(reason?: string): void {
    invalidate(this.#entry.key, reason);
  }

  dispose(): void {
    if (this.#disposed) return;
    this.#disposed = true;
    this.#entry.subscribers = Math.max(0, this.#entry.subscribers - 1);
  }
}

export function resource<T>(key: string, fetcher: Fetcher<T>): ResourceHandle<T> {
  return new ResourceHandle(getEntry(key, fetcher));
}

export function peek<T>(key: string): T | undefined {
  return (entries.get(key) as Entry<T> | undefined)?.state.data;
}

export function invalidate(key: string, reason?: string): void {
  const entry = entries.get(key);
  countInvalidation(key);
  if (!entry) return;
  entry.state.stale = true;
  if (entry.subscribers > 0) {
    void runFetch(entry, true).catch(() => undefined);
  }
}

export function invalidateMany(keys: string[], reason?: string): void {
  for (const key of Array.from(new Set(keys))) {
    invalidate(key, reason);
  }
}

export function refresh<T>(key: string): Promise<T> | undefined {
  const entry = entries.get(key) as Entry<T> | undefined;
  return entry ? runFetch(entry, true) : undefined;
}

export async function mutateJson<T = unknown>(
  path: string,
  options: FetchOptions,
  expectedInvalidations: string[] = []
): Promise<T> {
  const result = await fetchJson<T>(path, options);
  if (expectedInvalidations.length) {
    invalidateMany(expectedInvalidations, "expected mutation invalidation");
  }
  return result;
}

export function __resourceStats() {
  return Array.from(entries.values()).map((entry) => ({
    key: entry.key,
    subscribers: entry.subscribers,
    loading: entry.state.loading,
    stale: entry.state.stale
  }));
}
