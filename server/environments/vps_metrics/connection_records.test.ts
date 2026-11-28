import { mkdtempSync, rmSync } from 'node:fs';
import { request, type ClientRequest, type IncomingMessage } from 'node:http';
import type { AddressInfo } from 'node:net';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import Fastify, { type FastifyInstance } from 'fastify';
import { afterEach, beforeEach, expect, test } from 'vitest';
import { loadConfig } from '../../core/config.ts';
import { createReadAccess } from '../../core/database/index.ts';
import { installMiddleware } from '../../core/http/middleware.ts';
import { count_live_stream } from '../../core/http/live_streams/index.ts';
import type { StreamCloseRecord } from '../../core/http/live_streams/records.ts';
import type { Invocation } from '../../cli/contracts.ts';
import { parse_time } from './cli.ts';
import { metrics_config } from './configuration.ts';
import { Store } from './data/store.ts';
import { MetricsRuntime } from './runtime.ts';
import { create_metrics_routes } from './routes.ts';
import type { ConnectionRecords } from './connection_records.ts';

// Existing coverage reaches the metrics runtime only through scripted probes and a mocked
// composition, so none of it proves that a real stream close reaches the store and the read.
let directory: string;
let app: FastifyInstance;
let runtime: MetricsRuntime;
let address: string;
let wall = 1_800_000_000;
const open_requests: ClientRequest[] = [];

async function start(): Promise<void> {
  directory = mkdtempSync(join(tmpdir(), 'connection-records-'));
  runtime = new MetricsRuntime({
    path: join(directory, 'metrics.sqlite3'),
    config: metrics_config({ read_workers: 1 }),
    collector: null,
    wall_clock: () => wall,
  });
  await runtime.start();
  app = Fastify({ forceCloseConnections: false });
  installMiddleware(
    app,
    loadConfig(undefined, {
      PLAN_DB_PATH: join(directory, 'unused.sqlite3'),
      PLAN_TEST_MODE: '1',
    }),
    undefined,
    undefined,
    undefined,
    undefined,
    (record) => runtime.record_stream_close(record),
  );
  app.register(create_metrics_routes(runtime) as never, { prefix: '/api' });
  // These routes end their streams the way the real ones do: destroy on stop, end by themselves.
  app.addHook('preClose', async () => {
    for (const reply of live) reply.raw.destroy();
  });
  app.get('/stream/:mode', async (req, reply) => {
    const { mode } = req.params as { mode: string };
    reply.hijack();
    reply.raw.writeHead(200, { 'content-type': 'text/event-stream' });
    count_live_stream(reply);
    reply.raw.write('data: hello\n\n');
    live.add(reply);
    reply.raw.once('close', () => live.delete(reply));
    if (mode === 'server_end') setTimeout(() => reply.raw.end(), 20);
    if (mode === 'write_error')
      setTimeout(
        () => reply.raw.emit('error', Object.assign(new Error('x'), { code: 'EPIPE' })),
        20,
      );
    if (mode === 'write_error') setTimeout(() => reply.raw.destroy(), 40);
    // Other modes stay silent after the first frame. That is the frozen stream.
  });
  await app.listen({ port: 0, host: '127.0.0.1' });
  address = `http://127.0.0.1:${(app.server.address() as AddressInfo).port}`;
}
const live = new Set<{ raw: { destroy(): void } }>();
beforeEach(start);
afterEach(async () => {
  for (const req of open_requests.splice(0)) req.destroy();
  live.clear();
  await app.close().catch(() => {});
  await runtime.stop({ deadline: performance.now() / 1000 + 5 }).catch(() => {});
  rmSync(directory, { recursive: true, force: true });
});

function identity(device_id: string): string {
  return JSON.stringify({ kind: 'browser', device_id, version: '1' });
}
async function open_stream(mode: string, device_id: string): Promise<IncomingMessage> {
  return new Promise((resolve, reject) => {
    const req = request(
      `${address}/stream/${mode}`,
      { headers: { 'x-panels-client': identity(device_id) } },
      (res) => {
        res.on('error', () => {});
        res.once('data', () => resolve(res));
      },
    );
    req.on('error', () => {});
    req.once('error', reject);
    open_requests.push(req);
    req.end();
  });
}
async function closes(device_id: string, count = 1): Promise<StreamCloseRecord[]> {
  for (let attempt = 0; attempt < 100; attempt++) {
    const response = await fetch(
      `${address}/api/connection-records?device_id=${device_id}&since=0&until=4000000000000&limit=10`,
    );
    const records = (await response.json()) as ConnectionRecords;
    if (response.status !== 200) throw new Error(JSON.stringify(records));
    if (records.stream_closes.length >= count) return records.stream_closes;
    await delay(25);
  }
  throw new Error(`no stream close recorded for ${device_id}`);
}

test('each way a live stream ends is stored with the right closer', async () => {
  // Reset: the client drops the TCP connection without a goodbye.
  (await open_stream('plain', 'reset')).socket.resetAndDestroy();
  expect((await closes('reset'))[0]).toMatchObject({
    closed_by: 'peer',
    error_code: 'ECONNRESET',
    route: '/stream/:mode',
  });

  // Freeze: the server sends nothing more, the client gives up later. The gap is the evidence.
  const frozen = await open_stream('plain', 'freeze');
  await delay(300);
  frozen.destroy();
  const [freeze] = await closes('freeze');
  expect(freeze).toMatchObject({ closed_by: 'peer', client_kind: 'browser', bytes_written: 13 });
  expect(freeze!.last_write_ago_ms).toBeGreaterThanOrEqual(250);
  expect(freeze!.age_ms).toBeGreaterThanOrEqual(250);

  // The route finishes the response itself.
  await open_stream('server_end', 'server-end');
  expect((await closes('server-end'))[0]).toMatchObject({ closed_by: 'server_ended' });

  // A write fails on the response.
  await open_stream('write_error', 'write-error');
  expect((await closes('write-error'))[0]).toMatchObject({
    closed_by: 'write_error',
    error_code: 'EPIPE',
  });

  // The server stops and destroys what is open.
  await open_stream('plain', 'stop');
  await app.close();
  for (let attempt = 0; attempt < 100; attempt++) {
    const { stream_closes } = await runtime.connection_records({
      since: 0,
      until: 4_000_000_000_000,
      device_id: 'stop',
      limit: 10,
    });
    if (stream_closes.length) {
      expect(JSON.parse(JSON.stringify(stream_closes[0]))).toMatchObject({
        closed_by: 'server_pre_close',
      });
      return;
    }
    await delay(25);
  }
  throw new Error('no stream close recorded for stop');
});

test('client events are validated, stored beside the stream records and rate limited per device', async () => {
  const post = (device_id: string | null, body: unknown) =>
    fetch(`${address}/api/client-events`, {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        ...(device_id ? { 'x-panels-client': identity(device_id) } : {}),
      },
      body: JSON.stringify(body),
    });
  const batch = (reason: string) => ({
    version: 1,
    events: [
      {
        at: 1_799_999_999_000,
        kind: 'stream_ended',
        stream: '/api/changes',
        reason,
        open_ms: 5,
        close_code: 1006,
        last_seq: 41,
      },
    ],
  });
  expect((await post(null, batch('x'))).status).toBe(400);
  expect((await post('bad', { ...batch('x'), version: 2 })).status).toBe(422);
  expect(
    (
      await post('bad', {
        version: 1,
        events: Array.from({ length: 51 }, () => batch('x').events[0]),
      })
    ).status,
  ).toBe(422);
  expect((await post('bad', { version: 1, events: [{ at: 1, kind: 'bogus' }] })).status).toBe(422);
  expect(
    (await post('bad', { version: 1, events: [], padding: 'x'.repeat(17 * 1024) })).status,
  ).toBe(413);
  for (let index = 0; index < 12; index++)
    expect((await post('a', batch('ERR_NETWORK_CHANGED'))).status).toBe(200);
  expect((await post('a', batch('ERR_NETWORK_CHANGED'))).status).toBe(429);
  expect((await post('b', batch('ERR_CONNECTION_RESET'))).status).toBe(200);
  wall += 61;
  expect((await post('a', batch('ERR_NETWORK_CHANGED'))).status).toBe(200);

  let records: ConnectionRecords | undefined;
  for (let attempt = 0; attempt < 100; attempt++) {
    const response = await fetch(`${address}/api/connection-records?device_id=b&limit=5`);
    records = (await response.json()) as ConnectionRecords;
    if (records.client_events.length) break;
    await delay(25);
  }
  expect(records!.client_events).toMatchObject([
    {
      device_id: 'b',
      client_kind: 'browser',
      kind: 'stream_ended',
      stream: '/api/changes',
      reason: 'ERR_CONNECTION_RESET',
      open_ms: 5,
      close_code: 1006,
      last_seq: 41,
    },
  ]);
});

test('records older than 30 days are deleted and the rest read back oldest first', () => {
  const path = join(directory, 'retention.sqlite3');
  const config = metrics_config();
  const store = new Store(path, config);
  store.initialize();
  const now = 1_800_000_000;
  const day = 86_400_000;
  const record = (closed_at: number): StreamCloseRecord => ({
    route: '/api/changes',
    client_kind: 'desktop',
    device_id: 'mac',
    opened_at: closed_at - 1000,
    closed_at,
    age_ms: 1000,
    bytes_written: 1,
    closed_by: 'peer',
    last_write_ago_ms: 1,
    error_code: null,
  });
  const old = now * 1000 - 31 * day;
  const recent = now * 1000 - 29 * day;
  store.add_stream_close(record(recent));
  store.add_stream_close(record(old));
  store.add_client_events('mac', 'desktop', old, [{ at: old, kind: 'online' }]);
  store.add_client_events('mac', 'desktop', recent, [{ at: recent, kind: 'online' }]);
  store.prune(now);
  const reads = createReadAccess(path, { busyTimeoutMs: 100 });
  try {
    const kept = reads.read((sql) =>
      store.connection_records(sql, { since: 0, until: now * 1000, device_id: null, limit: 10 }),
    );
    expect(kept.stream_closes.map((row) => row.closed_at)).toEqual([recent]);
    expect(kept.client_events.map((row) => row.at)).toEqual([recent]);
  } finally {
    reads.close();
    store.close();
  }
});

test('a time with no offset is refused, because 01:50 must mean one instant', () => {
  const inv = {
    values: { as_json: false },
    command: { path: ['connection', 'records'] },
  } as never as Invocation;
  const now = Date.parse('2026-11-27T01:00:00Z');
  expect(parse_time(inv, '--since', '90m', now)).toBe(now - 90 * 60_000);
  expect(parse_time(inv, '--since', '2026-11-27T01:50:00+01:00', now)).toBe(
    Date.parse('2026-11-27T00:50:00Z'),
  );
  expect(() => parse_time(inv, '--since', '2026-11-27T01:50:00', now)).toThrow();
});
