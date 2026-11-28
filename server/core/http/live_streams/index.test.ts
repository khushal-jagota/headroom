import { Readable } from 'node:stream';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import Fastify, { type FastifyReply } from 'fastify';
import { afterEach, expect, test, vi } from 'vitest';
import { loadConfig } from '../../config.ts';
import { installMiddleware } from '../middleware.ts';
import { ClientLiveStreams, count_live_stream } from './index.ts';

afterEach(() => vi.restoreAllMocks());

// No existing coverage counts simultaneous responses, threshold excursions, or client isolation.
test('warns at 101 per identity, rearms at 100, and releases each response only once', () => {
  const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
  const streams = new ClientLiveStreams();
  const client = { device_id: 'desktop-one', kind: 'desktop' as const, version: '1' };
  const close = Array.from({ length: 100 }, () => streams.open(client, '/api/live'));
  // Each key component separates clients. Additional version facts do not split one client.
  for (const other of [
    { ...client, device_id: 'desktop-two' },
    { ...client, kind: 'browser' as const },
    { ...client, version: '2' },
  ]) {
    for (let index = 0; index < 100; index++) streams.open(other, '/api/live');
  }
  expect(warn).not.toHaveBeenCalled();
  const tail = streams.open({ ...client, bridge_version: 5 }, '/notifications/desktop');
  const warning = () => JSON.parse(warn.mock.calls.at(-1)![0].slice('client_live_streams '.length));
  expect(warning()).toEqual({
    client,
    total: 101,
    routes: { '/api/live': 100, '/notifications/desktop': 1 },
  });
  const extra = streams.open(client, '/api/live');
  tail();
  tail(); // finish followed by close must not decrement twice.
  expect(warn).toHaveBeenCalledTimes(1);
  extra();
  const again = streams.open(client, '/api/live');
  expect(warn).toHaveBeenCalledTimes(2);
  expect(warning().total).toBe(101);
  again();
  close.forEach((release) => release());
  for (let index = 0; index < 100; index++) streams.open(client, '/api/live');
  expect(warn).toHaveBeenCalledTimes(2);
  streams.open(client, '/api/live');
  expect(warn).toHaveBeenCalledTimes(3);
});

// Reuse the small Fastify/middleware host used by authority.test.ts; no socket or browser.
// This boundary proves that flushHeaders and hijack do not bypass response ownership.
test('counts normal, preflushed, and hijacked responses and releases on finish or disconnect', async () => {
  const app = Fastify();
  installMiddleware(
    app,
    loadConfig(undefined, {
      PLAN_DB_PATH: join(tmpdir(), 'unused-live-streams.sqlite'),
      PLAN_TEST_MODE: '1',
    }),
  );
  const original = ClientLiveStreams.prototype.open;
  const opened = vi.spyOn(ClientLiveStreams.prototype, 'open');
  const releases: ReturnType<typeof vi.fn>[] = [];
  opened.mockImplementation(function (this: ClientLiveStreams, client, route) {
    const close = vi.fn(original.call(this, client, route));
    releases.push(close);
    return close;
  });
  const replies: FastifyReply[] = [];
  app.get('/stream/:mode/:id', async (request, reply) => {
    const { mode } = request.params as { mode: string };
    replies.push(reply);
    if (mode === 'raw') {
      reply.hijack();
      reply.raw.writeHead(200, { 'content-type': 'text/event-stream' });
      count_live_stream(reply);
      count_live_stream(reply);
      return;
    }
    reply.type('text/event-stream');
    if (mode === 'flush') {
      reply.raw.setHeader('content-type', 'text/event-stream');
      reply.raw.flushHeaders();
    }
    return reply.send(new Readable({ read() {} }));
  });
  const headers = {
    'X-Panels-Client': JSON.stringify({ device_id: 'one', kind: 'browser', version: '1' }),
  };
  const responses: Promise<unknown>[] = [];
  try {
    for (const mode of ['normal', 'flush', 'raw']) {
      responses.push(
        Promise.resolve(app.inject({ url: `/stream/${mode}/one`, headers })).catch(
          (error) => error,
        ),
      );
    }
    await vi.waitFor(() => expect(opened).toHaveBeenCalledTimes(3));
    expect(opened.mock.calls.every(([, route]) => route === '/stream/:mode/:id')).toBe(true);
    replies[0]!.raw.end();
    await vi.waitFor(() => expect(releases[0]).toHaveBeenCalled());
    replies[1]!.raw.emit('close');
    expect(releases[1]).toHaveBeenCalled();
    expect(await responses[1]).toBeInstanceOf(Error);
    replies[1]!.raw.end();
    replies[2]!.raw.end();
    await Promise.all(responses);
    const unidentified = app.inject({ url: '/stream/raw/unidentified' });
    await vi.waitFor(() => expect(replies).toHaveLength(4));
    replies[3]!.raw.end();
    await unidentified;
    expect(opened).toHaveBeenCalledTimes(3);
  } finally {
    for (const reply of replies) reply.raw.end();
    await Promise.all(responses);
    await app.close();
  }
});
