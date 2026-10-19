import { QueryClient, QueryObserver } from '@tanstack/svelte-query';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { createResourceCoordinator } from '../src/lib/resourceVersions/coordinator';
import { resourceQuery } from '../src/lib/resourceVersions/query';
import { boundInactiveQueries } from '../src/lib/resourceVersions/cache';

const clients: QueryClient[] = [];
function client() {
  const result = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  clients.push(result); return result;
}
afterEach(() => { clients.forEach(item => item.clear()); clients.length = 0; vi.unstubAllGlobals(); });

function response(body: unknown) { return new Response(JSON.stringify(body)); }
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(done => { resolve = done; });
  return { promise, resolve };
}

describe('resource version reconciliation', () => {
  it('reads only changed bodies, retains legacy fallback, and reconciles a server epoch reset', async () => {
    const qc = client();
    let epoch = 'one'; let a = '1'; let b = '1';
    const calls: string[] = [];
    vi.stubGlobal('fetch', vi.fn(async (input: string) => {
      const url = new URL(input, 'http://panels'); calls.push(url.pathname + ':' + (url.searchParams.get('key') ?? ''));
      if (url.pathname === '/api/resource-versions') return response({ epoch, resources:
        JSON.parse(url.searchParams.get('keys')!).map((key: string) => ({ key, revision: key === '/api/a' ? a : b })) });
      if (url.pathname === '/api/resource-body') {
        const key = url.searchParams.get('key'); return response({ epoch, key, revision: key === '/api/a' ? a : b, body: { value: key === '/api/a' ? a : b } });
      }
      return response({ legacy: true });
    }));
    const observers = [new QueryObserver(qc, resourceQuery(['a'], '/api/a', true)),
      new QueryObserver(qc, resourceQuery(['b'], '/api/b', true)),
      new QueryObserver(qc, resourceQuery(['legacy'], '/api/legacy'))];
    const unsubscribes = observers.map(observer => observer.subscribe(() => {}));
    await Promise.all(observers.map(observer => observer.refetch())); calls.length = 0;
    const coordinator = createResourceCoordinator(qc);
    await coordinator.refresh();
    expect(calls).toEqual(['/api/resource-versions:', '/api/legacy:']);
    calls.length = 0; a = '2'; await coordinator.refresh();
    expect(calls).toEqual(['/api/resource-versions:', '/api/resource-body:/api/a', '/api/legacy:']);
    expect(observers[0].getCurrentResult().data).toEqual({ value: '2' });
    calls.length = 0; epoch = 'two'; await coordinator.refresh();
    expect(calls.filter(call => call.startsWith('/api/resource-body'))).toHaveLength(2);
    unsubscribes.forEach(stop => stop()); coordinator.stop();
  });

  it('bounds bursts to one pending probe and stops obsolete callbacks', async () => {
    const qc = client(); const pending = deferred<Response>(); let probes = 0; let reads = 0;
    vi.stubGlobal('fetch', vi.fn(async (input: string) => {
      const url = new URL(input, 'http://panels');
      if (url.pathname === '/api/resource-versions') {
        probes++; if (probes === 1) return pending.promise;
        return response({ epoch: 'e', resources: [{ key: '/api/a', revision: '1' }] });
      }
      reads++; return response({ epoch: 'e', key: '/api/a', revision: '1', body: 1 });
    }));
    const observer = new QueryObserver(qc, resourceQuery(['a'], '/api/a', true));
    const stop = observer.subscribe(() => {}); await observer.refetch();
    const coordinator = createResourceCoordinator(qc); const round = coordinator.refresh();
    await Promise.resolve();
    for (let i = 0; i < 100; i++) void coordinator.refresh();
    pending.resolve(response({ epoch: 'e', resources: [{ key: '/api/a', revision: '1' }] }));
    await round; expect(probes).toBe(2); expect(reads).toBe(1);
    const held = deferred<Response>();
    vi.stubGlobal('fetch', vi.fn(() => held.promise));
    const obsolete = coordinator.refresh(); await Promise.resolve(); coordinator.stop();
    held.resolve(response({ epoch: 'e', resources: [{ key: '/api/a', revision: '2' }] }));
    await obsolete; expect(reads).toBe(1); expect(observer.getCurrentResult().data).toBe(1); stop();
  });

  it('cancels an initial older read when a probe already observes a newer source', async () => {
    const qc = client(); const initial = deferred<Response>(); let reads = 0;
    vi.stubGlobal('fetch', vi.fn(async (input: string) => {
      if (input.startsWith('/api/resource-versions')) {
        return response({ epoch: 'e', resources: [{ key: '/api/a', revision: '2' }] });
      }
      reads++;
      if (reads === 1) return initial.promise;
      return response({ epoch: 'e', key: '/api/a', revision: '2', body: 'new' });
    }));
    const observer = new QueryObserver(qc, resourceQuery<string>(['a'], '/api/a', true));
    const stop = observer.subscribe(() => {});
    const coordinator = createResourceCoordinator(qc);
    await coordinator.refresh();
    expect(observer.getCurrentResult().data).toBe('new');
    initial.resolve(response({ epoch: 'e', key: '/api/a', revision: '1', body: 'old' }));
    await initial.promise; await Promise.resolve();
    expect(qc.getQueryData(['a'])).toMatchObject({ body: 'new', revision: '2' });
    expect(reads).toBe(2);
    stop(); coordinator.stop();
  });

  it('never promotes a probe version onto an older body admitted during the request', async () => {
    const qc = client(); const probe = deferred<Response>(); let revision = '1';
    vi.stubGlobal('fetch', vi.fn(async (input: string) => {
      if (input.startsWith('/api/resource-versions')) return probe.promise;
      return response({ epoch: 'e', key: '/api/a', revision, body: revision });
    }));
    const observer = new QueryObserver(qc, resourceQuery<string>(['a'], '/api/a', true));
    const stop = observer.subscribe(() => {}); await observer.refetch();
    const coordinator = createResourceCoordinator(qc); const round = coordinator.refresh();
    await Promise.resolve(); revision = '2';
    probe.resolve(response({ epoch: 'e', resources: [{ key: '/api/a', revision: '2' }] }));
    await round;
    expect(observer.getCurrentResult().data).toBe('2');
    expect(qc.getQueryData(['a'])).toMatchObject({ body: '2', revision: '2' });
    stop(); coordinator.stop();
  });
});

it('evicts inactive decoded bodies by both count and bytes without evicting active data or drafts', async () => {
  const qc = client(); const unsubscribe = boundInactiveQueries(qc, { entries: 2, bytes: 10 });
  const add = async (key: string, size: number) => {
    const observer = new QueryObserver(qc, { queryKey: [key], meta: { resourceBody: true },
      queryFn: async () => ({ body: key, decodedBytes: size }) });
    const stop = observer.subscribe(() => {}); await observer.refetch(); return stop;
  };
  qc.setQueryData(['draft'], 'unsent');
  const active = await add('active', 100);
  (await add('old', 4))(); (await add('new', 4))(); (await add('newest', 4))();
  expect(qc.getQueryData(['old'])).toBeUndefined();
  expect(qc.getQueryData(['new'])).toBeDefined();
  expect(qc.getQueryData(['active'])).toBeDefined();
  (await add('oversized', 11))();
  expect(qc.getQueryData(['oversized'])).toBeUndefined();
  active(); expect(qc.getQueryData(['active'])).toBeUndefined();
  expect(qc.getQueryData(['draft'])).toBe('unsent'); unsubscribe();
});
