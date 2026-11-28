import Fastify from 'fastify';
import { once } from 'node:events';
import { afterEach, beforeEach, expect, test } from 'vitest';
import WebSocket from 'ws';
import { fixture } from './ticket_fixture.ts';
import { SignalPublicationCoordinator } from '../conversation/publication/coordinator.ts';
import { createSignalNavigationRuntime } from '../navigation/index.ts';
import { registerArea } from '../core/authority/register.ts';
import { buildClock } from '../core/clock.ts';
import { loadConfig } from '../core/config.ts';
import { ChangeSignal } from '../core/changes/index.ts';
import type { StreamCloseRecord } from '../core/http/live_streams/records.ts';
import { LiveSubscriptions } from '../live_updates/registry.ts';
import { install_live_socket } from '../live_updates/socket.ts';
import type { LiveEnvelope } from '../live_updates/contracts.ts';
import type { CurrentViewSnapshot } from '../../web/src/lib/navigation/contracts.ts';

// Distinct gap: no test opened a real WebSocket on the real admission, the navigation producer
// and the close record. This host is the navigation admission test's host plus the live socket.
// It needs no browser, because the Mac 1 proof already covered the network path.
const LOGIN = 'owner@example.test';
const WEB_ORIGIN = 'https://panels.example.test';
const view = JSON.stringify({
  projection_version: 7,
  scope_generation: 1,
  locale: 'en-US',
  selected: { kind: 'chief', id: 'chief' },
  selected_conversation_id: null,
  review_conversation_ids: [],
  review_skipped_keys: [],
  review_priority_keys: [],
  windows: [],
});
const browser = { kind: 'browser', device_id: 'tab-one', version: '1' };

let app: ReturnType<typeof Fastify>;
let changes: ChangeSignal;
let navigation: ReturnType<typeof createSignalNavigationRuntime>;
let producer: SignalPublicationCoordinator;
let address: string;
const closes: StreamCloseRecord[] = [];

beforeEach(async () => {
  closes.length = 0;
  let attached = false;
  const database = fixture({
    commitSteps: [
      (tx) => producer?.finalize_signal(tx),
      (tx) => {
        if (attached) navigation.navigationCommitStep(tx);
      },
    ],
  });
  database.write((tx) => {
    tx.run("INSERT INTO navigation_meta VALUES(1,'source',7,'view-epoch',0)");
    tx.run('INSERT INTO chief_page(singleton) VALUES(1)');
  });
  producer = new SignalPublicationCoordinator({ database, require_runtime_ownership: () => {} });
  producer.bind_runtime(() => null);
  producer.initialize();
  navigation = createSignalNavigationRuntime(producer);
  navigation.attach();
  attached = true;
  const config = loadConfig(undefined, {
    PLAN_TEST_MODE: '1',
    PLAN_TRUSTED_INGRESS_PROVIDER: 'tailscale',
    PLAN_TRUSTED_INGRESS_ALLOWED_LOGIN: LOGIN,
    PLAN_TRUSTED_INGRESS_CANONICAL_ORIGIN: WEB_ORIGIN,
  });
  const clock = buildClock(config);
  changes = new ChangeSignal();
  const subscriptions = new LiveSubscriptions();
  app = Fastify({ forceCloseConnections: true });
  install_live_socket(app, {
    config,
    database,
    clock,
    changes,
    subscriptions,
    stream_close_sink: (record) => closes.push(record),
  });
  registerArea(
    app,
    { prefix: '/api/navigation/view', routes: navigation.routes },
    { database, config, clock, signalAuthority: producer, live_subscriptions: subscriptions },
  );
  await app.ready();
  await navigation.start();
  address = (await app.listen({ host: '127.0.0.1', port: 0 })).replace('http', 'ws');
});
afterEach(async () => {
  await app.close();
  navigation.close();
});

const headers = (extra: Record<string, string> = {}) => ({
  'Tailscale-User-Login': LOGIN,
  ...extra,
});
/** Collects every frame in order, so that a test reads them with their sequence numbers. */
function connect(extra: Record<string, string> = headers(), identity: object = browser) {
  const socket = new WebSocket(address + '/api/live', { headers: extra });
  const frames: LiveEnvelope[] = [];
  const waiting: Array<() => void> = [];
  socket.on('message', (data) => {
    frames.push(JSON.parse(String(data)) as LiveEnvelope);
    waiting.splice(0).forEach((wake) => wake());
  });
  const next = async (index: number): Promise<LiveEnvelope> => {
    while (frames.length <= index) await new Promise<void>((resolve) => waiting.push(resolve));
    return frames[index]!;
  };
  const closed = once(socket, 'close').then(([code]) => code as number);
  const hello = async () => {
    await once(socket, 'open');
    socket.send(JSON.stringify({ op: 'hello', client: identity }));
  };
  return { socket, frames, next, closed, hello };
}
async function refusal(extra: Record<string, string>): Promise<number> {
  const socket = new WebSocket(address + '/api/live', { headers: extra });
  const [, response] = await once(socket, 'unexpected-response');
  return (response as { statusCode: number }).statusCode;
}

test('the upgrade needs the login and an allowed origin, and a socket with no origin is native', async () => {
  expect(await refusal({})).toBe(403);
  expect(await refusal(headers({ Origin: 'https://evil.example.test' }))).toBe(403);
  for (const origin of [WEB_ORIGIN, 'panels://app', undefined]) {
    const client = connect(headers(origin ? { Origin: origin } : {}));
    await client.hello();
    expect((await client.next(0)).kind).toBe('ready');
    client.socket.close();
    await client.closed;
  }
});

test('the first frame decides: a bad one and an old desktop get their named closes', async () => {
  const bad = connect();
  await once(bad.socket, 'open');
  bad.socket.send('{"op":"subscribe"}');
  expect(await bad.closed).toBe(4003);
  const old = connect(headers(), {
    kind: 'desktop',
    device_id: 'mac',
    version: '1',
    bridge_version: 1,
  });
  await old.hello();
  expect(await old.closed).toBe(4001);
});

test('a subscription carries the snapshot, a failed one leaves the socket open, and the close is recorded', async () => {
  const read = await fetch(
    `${address.replace('ws', 'http')}/api/navigation/view/snapshot?view=${encodeURIComponent(view)}`,
    { headers: headers(), signal: AbortSignal.timeout(5000) },
  );
  const old = await read.json();

  const client = connect();
  await client.hello();
  expect(await client.next(0)).toMatchObject({ seq: 1, sub: null, kind: 'ready' });
  client.socket.send(
    JSON.stringify({ op: 'subscribe', id: 'nav', kind: 'navigation', params: { view } }),
  );
  const snapshot = await client.next(1);
  expect(snapshot).toMatchObject({ seq: 2, sub: 'nav', kind: 'snapshot' });
  expect(snapshot.data).toEqual(old);

  client.socket.send(
    JSON.stringify({ op: 'subscribe', id: 'bad', kind: 'navigation', params: {} }),
  );
  expect(await client.next(2)).toMatchObject({
    seq: 3,
    sub: 'bad',
    kind: 'ended',
    data: { status: 422 },
  });
  changes.emit();
  expect(await client.next(3)).toMatchObject({ seq: 4, sub: null, kind: 'change' });

  client.socket.close();
  expect(await client.closed).toBe(1005);
  await new Promise((resolve) => setTimeout(resolve, 50));
  expect(closes.at(-1)).toMatchObject({
    route: '/api/live',
    client_kind: 'browser',
    device_id: 'tab-one',
    closed_by: 'peer',
    close_code: 1005,
    last_sequence: 4,
  });
});

test('a stopping server closes its sockets with "server restarting"', async () => {
  const client = connect();
  await client.hello();
  await client.next(0);
  const closing = app.close();
  expect(await client.closed).toBe(4002);
  await closing;
  expect(closes.at(-1)).toMatchObject({ closed_by: 'server_pre_close', close_code: 4002 });
});

// Distinct gap: a permit assertion cannot prove that the real delivery ends. The producer is
// revoked for real, and the same host then admits a fresh snapshot after reattachment.
test('revoking the producer ends an open navigation subscription and reattachment admits a fresh snapshot', async () => {
  const client = connect();
  await client.hello();
  await client.next(0);
  const subscribe = (id: string) =>
    client.socket.send(
      JSON.stringify({ op: 'subscribe', id, kind: 'navigation', params: { view } }),
    );
  subscribe('before');
  const old = (await client.next(1)).data as CurrentViewSnapshot;
  producer.invalidate('detached');
  expect(await client.next(2)).toMatchObject({ sub: 'before', kind: 'ended' });
  expect(() => producer.validate(old.authority)).toThrow();
  navigation.close();
  navigation.attach();
  subscribe('after');
  const fresh = (await client.next(3)).data as CurrentViewSnapshot;
  expect(fresh.mode).toBe('current');
  expect(fresh.authority.permit.installation_id).not.toBe(old.authority.permit.installation_id);
  expect(fresh.authority.delivery_session_id).toBe(old.authority.delivery_session_id);
});
