import { QueryObserver } from '@tanstack/svelte-query';
import { afterEach, expect, it, vi } from 'vitest';
import { mutateJson, mutateJsonAndWaitForScreen } from '../src/lib/mutate';
import { queryClient } from '../src/lib/queryClient';
import { SCREEN_REFRESH_WAIT_MS } from '../src/lib/requestLimit/config';
import { resourceQuery } from '../src/lib/resourceVersions/query';
import { stopScreenRefresh } from '../src/lib/resourceVersions/refresh';

afterEach(() => { stopScreenRefresh(); queryClient.clear(); vi.useRealTimers(); vi.unstubAllGlobals(); });

// Risk: the write door waited for every read on the page, so one read with no answer
// kept every control busy after the server accepted the write.
it('ends a write at the server answer while a read on the page never answers', async () => {
  let reads = 0; let answers = true;
  vi.stubGlobal('fetch', vi.fn(async (_input: string, init?: RequestInit) => {
    if (init?.method === 'POST') return new Response(JSON.stringify({ saved: true }));
    reads++;
    return answers ? new Response(JSON.stringify({ value: reads })) : new Promise<Response>(() => {});
  }));
  const observer = new QueryObserver(queryClient, resourceQuery<{ value: number }>(['page'], '/api/page'));
  const stop = observer.subscribe(() => {});
  await observer.refetch();
  // A control that closes on save has the fresh read when its wait ends.
  await mutateJsonAndWaitForScreen('/api/thing', { method: 'POST', body: {} });
  expect(observer.getCurrentResult().data).toEqual({ value: 2 });

  answers = false;
  await expect(mutateJson('/api/thing', { method: 'POST', body: {} })).resolves.toEqual({ saved: true });
  // The refresh did start. Nothing waited for it.
  await vi.waitFor(() => expect(reads).toBe(3));

  // With a read that never answers, that control waits only for the stated limit.
  vi.useFakeTimers();
  let closed = false;
  const saved = mutateJsonAndWaitForScreen('/api/thing', { method: 'POST', body: {} }).then(() => { closed = true; });
  await vi.advanceTimersByTimeAsync(SCREEN_REFRESH_WAIT_MS - 1);
  expect(closed).toBe(false);
  await vi.advanceTimersByTimeAsync(1);
  await saved;
  expect(closed).toBe(true);
  stop();
});
