import { afterEach, expect, it, vi } from 'vitest';
import { fetchJson } from '../src/lib/api';
import { theServerRefusedTheSend } from '../src/lib/conversation/outgoing';
import { REQUEST_LIMIT_MS } from '../src/lib/requestLimit/config';

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

// Risk: a request that gets no answer waited without end, and held whatever waited for it.
it('ends a request with no answer at its limit, as no answer and not as a refusal', async () => {
  vi.useFakeTimers();
  vi.stubGlobal('fetch', vi.fn((_input: string, init?: RequestInit) => new Promise<Response>((_resolve, reject) => {
    init?.signal?.addEventListener('abort', () => reject(init.signal!.reason));
  })));
  const failure = (request: Promise<unknown>) => request.then(() => null, (error: unknown) => error);
  const read = failure(fetchJson('/api/slow'));
  const write = failure(fetchJson('/api/slow', { method: 'POST', body: {}, limitMs: 100 }));
  await vi.advanceTimersByTimeAsync(100);
  const unanswered = await write;
  expect(unanswered).toMatchObject({ code: 'timeout', message: expect.stringContaining('Check the result') });
  expect(theServerRefusedTheSend(unanswered)).toBe(false);
  await vi.advanceTimersByTimeAsync(REQUEST_LIMIT_MS - 101);
  await expect(Promise.race([read, Promise.resolve('waiting')])).resolves.toBe('waiting');
  await vi.advanceTimersByTimeAsync(1);
  expect(await read).toMatchObject({ code: 'timeout', message: 'The server did not answer in time.' });
  // The limit covers a body that stops after the response headers.
  vi.stubGlobal('fetch', vi.fn(async (_input: string, init?: RequestInit) => new Response(new ReadableStream({
    start(stream) { init?.signal?.addEventListener('abort', () => stream.error(init.signal!.reason)); }
  }))));
  const stalled = failure(fetchJson('/api/slow', { limitMs: 100 }));
  await vi.advanceTimersByTimeAsync(100);
  expect(await stalled).toMatchObject({ code: 'timeout' });
  vi.stubGlobal('fetch', vi.fn((_input: string, init?: RequestInit) => new Promise<Response>((_resolve, reject) => {
    init?.signal?.addEventListener('abort', () => reject(init.signal!.reason));
  })));
  // The caller's own abort stays what it was.
  const caller = new AbortController();
  const abandoned = failure(fetchJson('/api/slow', { signal: caller.signal }));
  caller.abort();
  expect(await abandoned).toMatchObject({ code: 'network' });
});
