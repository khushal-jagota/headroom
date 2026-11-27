import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import Fastify from 'fastify';
import { afterEach, expect, test, vi } from 'vitest';
import { SSE_HEARTBEAT_MS, loadConfig } from './config.ts';
import { buildClock } from './clock.ts';
import { installMiddleware } from './http/middleware.ts';
import { registerCoreRoutes } from './routes.ts';

afterEach(() => vi.useRealTimers());

// Distinct risk: the page cannot see an SSE comment, so a silent stream looks the same as a
// quiet one, and a missing `retry:` leaves the browser's own 3 s reconnect delay. Nothing else
// reads the first bytes of `/api/changes`. A real socket is needed because inject buffers.
test('/api/changes starts with the retry line and sends a named heartbeat that is not a change', async () => {
  const root = mkdtempSync(join(tmpdir(), 'panels-changes-stream-'));
  const app = Fastify();
  const config = loadConfig(undefined, {
    PLAN_DB_PATH: join(root, 'unused.sqlite'),
    PLAN_TEST_MODE: '1',
  });
  installMiddleware(app, config);
  await registerCoreRoutes(
    app,
    config,
    buildClock(config),
    { subscribe: () => () => {}, emit() {} },
    root,
    undefined as never,
  );
  await app.listen({ port: 0, host: '127.0.0.1' });
  vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] });
  const abort = new AbortController();
  try {
    const address = app.server.address();
    const port = typeof address === 'object' && address ? address.port : 0;
    const response = await fetch(`http://127.0.0.1:${port}/api/changes`, { signal: abort.signal });
    expect(response.headers.get('content-type')).toContain('text/event-stream');
    const reader = response.body!.getReader();
    const decoder = new TextDecoder();
    const first = await reader.read();
    expect(decoder.decode(first.value)).toBe('retry: 1000\n\n');
    vi.advanceTimersByTime(SSE_HEARTBEAT_MS);
    const beat = decoder.decode((await reader.read()).value);
    expect(beat).toMatch(/^event: heartbeat\ndata: \d{4}-\d\d-\d\dT[\d:.]+Z\n\n$/);
    expect(beat).not.toContain('data: change');
  } finally {
    abort.abort();
    await app.close();
    rmSync(root, { recursive: true, force: true });
  }
});
